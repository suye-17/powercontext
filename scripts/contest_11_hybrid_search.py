"""Compare candidate and frozen Memory.search on one real seekdb database.

Input JSON has documents [{id, text, embedding}] and queries
[{text, embedding, relevant_document_ids}]. Embeddings are 384-dimensional
unit vectors. Labels remain in this evaluation process, outside product code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from sqlalchemy import event

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput, MemoryQueryEmbedding
from powercontext.builtin.artifacts.memory.canonical import normalize_text, validate_embedding
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex
from powercontext.builtin.persistence.oceanbase.memory_index import OceanBaseMemoryFTSIndex, OceanBaseMemoryVectorIndex
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.relational import RelationalContexts

_PROFILE = EmbeddingProfile(profile_id="contest-11-offline", model="fixed-input", dimension=384)


def _vector(values):
    vector = validate_embedding(tuple(values), dimension=384)
    if not math.isclose(sum(value * value for value in vector), 1.0, abs_tol=1e-4):
        raise ValueError("Input embedding must be a unit vector")  # noqa: TRY003
    return vector


class _Embedding:
    profile = _PROFILE

    def __init__(self, documents):
        self.vectors = {document["text"]: _vector(document["embedding"]) for document in documents}

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=tuple(self.vectors[text] for text in texts))


@contextmanager
def _candidate_statements(database):
    statements = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        query = statement.lower()
        if query.lstrip().startswith("select") and ("against" in query or "l2_distance" in query):
            statements.append((statement, parameters))

    event.listen(database.engine.sync_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", record)


def _scores(ids, relevant):
    recall = len(set(ids) & relevant) / len(relevant)
    mrr = next((1.0 / rank for rank, doc_id in enumerate(ids, start=1) if doc_id in relevant), 0.0)
    return recall, mrr


async def _checked_ids(service, memory, result, document_ids):
    identities = [
        (hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id) for hit in result.hits
    ]
    if len(identities) != len(set(identities)):
        raise AssertionError("Duplicate hit identity")  # noqa: TRY003
    versions = await service.expand(result.hits)
    ids = []
    for hit, entry in zip(result.hits, versions, strict=True):
        if hit.memory_ref != memory.as_ref() or hit.text != entry.text:
            raise AssertionError("Incorrect current reference or exact body")  # noqa: TRY003
        if not hit.matched_by or not set(hit.matched_by) <= {"fts", "vector"}:
            raise AssertionError("Invalid candidate channels")  # noqa: TRY003
        ids.append(document_ids[entry.text])
    return ids


async def _measure(service, memory, query, database):
    embedding = MemoryQueryEmbedding(query_vector=_vector(query["embedding"]), embedding_profile=_PROFILE)
    with _candidate_statements(database) as statements:
        started = perf_counter()
        result = await service.search(
            query["text"],
            memories=(memory,),
            mode="hybrid",
            limit=10,
            query_embedding=embedding,
        )
        elapsed = (perf_counter() - started) * 1000
    return result, elapsed, statements


async def _scope_pairs(contexts, control, scope, documents, queries):
    service = (await contexts.get(scope)).artifacts.memory
    frozen = (await control.get(scope)).artifacts.memory
    memory = await service.remember(
        memory=None,
        entries=tuple(MemoryEntryInput(kind="fact", text=document["text"]) for document in documents),
        mode="append",
    )
    if memory is None:
        raise AssertionError("No Memory created")  # noqa: TRY003
    document_ids = {document["text"]: document["id"] for document in documents}
    pairs = []
    explain = None
    for number, query in enumerate(queries):
        relevant = set(query["relevant_document_ids"])
        if not relevant or not relevant <= set(document_ids.values()):
            raise ValueError("Each query needs nonempty labels belonging to the corpus")  # noqa: TRY003
        # Warm both paths on the same query, then alternate the measured order.
        await _measure(service, memory, query, contexts.database)
        await _measure(frozen, memory, query, contexts.database)
        measured = {}
        order = (("candidate", service), ("frozen", frozen))
        for name, selected in order if number % 2 == 0 else reversed(order):
            measured[name] = await _measure(selected, memory, query, contexts.database)
        candidate, elapsed, statements = measured["candidate"]
        baseline, control_elapsed, control_statements = measured["frozen"]
        if len(statements) != 1 or len(control_statements) != 2:
            raise AssertionError("Candidate statement count must be 1 versus frozen 2")  # noqa: TRY003
        ids = await _checked_ids(service, memory, candidate, document_ids)
        baseline_ids = await _checked_ids(frozen, memory, baseline, document_ids)
        recall, mrr = _scores(ids, relevant)
        baseline_recall, baseline_mrr = _scores(baseline_ids, relevant)
        if explain is None:
            statement, parameters = statements[0]
            async with contexts.database.transaction() as connection:
                plan = await connection.exec_driver_sql("EXPLAIN " + statement, parameters)
                explain = "\n".join(str(row[0]) for row in plan.all())
            if "TEXT RETRIEVAL SCAN" not in explain or "VECTOR INDEX" not in explain:
                raise AssertionError("Both native index operators are required")  # noqa: TRY003
        pairs.append({
            "scope": scope,
            "query": query["text"],
            "recall_at_10": recall,
            "mrr_at_10": mrr,
            "frozen_recall_at_10": baseline_recall,
            "frozen_mrr_at_10": baseline_mrr,
            "latency_ms": elapsed,
            "frozen_latency_ms": control_elapsed,
            "quality_nonregression": recall >= baseline_recall and mrr >= baseline_mrr,
            "complete_tuple_equal": candidate.hits == baseline.hits,
        })
    return pairs, explain


async def evaluate(dataset, scopes):
    documents = [{**document, "text": normalize_text(document["text"])} for document in dataset["documents"]]
    queries = dataset["queries"]
    if not documents or not queries or scopes < 1:
        raise ValueError("A nonempty corpus, query set, and positive scope count are required")  # noqa: TRY003
    for field in ("id", "text"):
        if len({document[field] for document in documents}) != len(documents):
            raise ValueError(f"Document {field} values must be unique")  # noqa: TRY003
    embedding = _Embedding(documents)
    pairs = []
    plans = []
    with TemporaryDirectory(prefix="pc11-compare-", dir="/tmp") as directory:
        async with open_builtin_contexts(
            BuiltinConfig(database=SeekDBConfig(path=Path(directory) / "db")),
            embedding_model=embedding,
        ) as contexts:
            control = RelationalContexts(
                database=contexts.database,
                index=CompositeMemoryIndex(OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(_PROFILE)),
                embedding_model=embedding,
            )
            try:
                for number in range(scopes):
                    scope_pairs, plan = await _scope_pairs(
                        contexts,
                        control,
                        f"comparison-{number}",
                        documents,
                        queries,
                    )
                    pairs.extend(scope_pairs)
                    plans.append(plan)
            finally:
                await control.aclose_usage_recorder()
    median = statistics.median(pair["latency_ms"] for pair in pairs)
    recall = statistics.mean(pair["recall_at_10"] for pair in pairs)
    return {
        "dataset": dataset.get("name", "supplied-input"),
        "environment": {
            "python": sys.version.split()[0],
            "pylibseekdb": version("pylibseekdb"),
            "sqlalchemy": version("sqlalchemy"),
            "pyobvector": version("pyobvector"),
        },
        "documents": len(documents),
        "queries": len(queries),
        "scopes": scopes,
        "measured_queries": len(pairs),
        "candidate_statements_per_query": 1,
        "frozen_candidate_statements_per_query": 2,
        "all_hit_checks_passed": True,
        "per_query_quality_nonregression": all(pair["quality_nonregression"] for pair in pairs),
        "evidence_recall_at_10": recall,
        "mrr_at_10": statistics.mean(pair["mrr_at_10"] for pair in pairs),
        "p50_latency_ms": median,
        "median_latency_ratio": median / statistics.median(pair["frozen_latency_ms"] for pair in pairs),
        "complete_tuple_equal_rate": statistics.mean(pair["complete_tuple_equal"] for pair in pairs),
        "numeric_gates_passed": recall >= 0.95
        and median <= 500
        and all(pair["quality_nonregression"] for pair in pairs),
        "official_acceptance": False,
        "pairs": pairs,
        "explain": plans,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scopes", type=int, default=3)
    args = parser.parse_args()
    report = asyncio.run(evaluate(json.loads(args.dataset.read_text(encoding="utf-8")), args.scopes))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({key: value for key, value in report.items() if key not in {"pairs", "explain"}}, ensure_ascii=False)
    )
    return 0 if report["numeric_gates_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

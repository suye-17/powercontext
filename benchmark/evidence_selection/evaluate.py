"""Independent offline scoring; evidence labels never enter a product request."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest, prepared_evidence
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates

SCOPE = "benchmark:scifact"
MEMORY = ArtifactRef(family="memory", artifact_id="memory", revision=1)
_TOKENS = re.compile(r"[a-z]+|\d+")


def merge_intervals(intervals: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def score_intervals(text: str, gold: Sequence[Sequence[int]], selected: Sequence[Sequence[int]]) -> tuple[float, bool]:
    expected = merge_intervals(gold)
    actual = merge_intervals(selected)
    gold_tokens = Counter(token for start, end in expected for token in _TOKENS.findall(text[start:end].lower()))
    selected_tokens = Counter(token for start, end in actual for token in _TOKENS.findall(text[start:end].lower()))
    count = sum(gold_tokens.values())
    recall = sum((gold_tokens & selected_tokens).values()) / count if count else 1.0
    complete = all(any(a <= start and b >= end for a, b in actual) for start, end in expected)
    return recall, complete


def entry_version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluate_case(case: dict[str, Any], budget: int) -> dict[str, Any]:
    hits = tuple(
        MemoryHit(
            memory_ref=MEMORY,
            entry_id=doc["entry_id"],
            entry_version_id=entry_version(doc["text"]),
            text=doc["text"],
            score=1.0,
            matched_by=("fts",),
        )
        for doc in case["candidates"]
    )
    start = time.perf_counter()
    build = PreparedContextBuilder().build_scopes_result(
        current_scope_id=SCOPE,
        memory_candidates=(PreparedMemoryCandidates(SCOPE, MEMORY, hits),),
        request=PrepareContextRequest(query=case["query"], max_bytes=budget, evidence_selection=True),
    )
    elapsed = time.perf_counter() - start
    originals = {doc["entry_id"]: doc["text"] for doc in case["candidates"]}
    for fragment, origin in zip(build.fragments, build.origins, strict=True):
        assert fragment.address == origin  # noqa: S101
        assert fragment.text == originals[fragment.address.entry_id][fragment.start : fragment.end]  # noqa: S101
    assert build.context.content_bytes == len((build.context.content or "").encode("utf-8")) <= budget  # noqa: S101
    selected = [(f.start, f.end) for f in build.fragments if f.address.entry_id == case["gold"]["entry_id"]]
    recall, complete = score_intervals(originals[case["gold"]["entry_id"]], case["gold"]["intervals"], selected)
    body_bytes = sum(len(f.text.encode("utf-8")) for f in build.fragments)
    return {
        "id": case["id"],
        "budget": budget,
        "recall": recall,
        "complete": complete,
        "content_bytes": build.context.content_bytes,
        "body_bytes": body_bytes,
        "format_bytes": build.context.content_bytes - body_bytes,
        "fragments": len(build.fragments),
        "gold_selected": bool(selected),
        "selected_entries": [f.address.entry_id for f in build.fragments],
        "selected_intervals": [
            {
                "scope_id": f.address.memory.scope_id,
                "memory_ref": f.address.memory.artifact.model_dump(mode="json"),
                "entry_id": f.address.entry_id,
                "entry_version_id": f.address.entry_version_id,
                "start": f.start,
                "end": f.end,
            }
            for f in build.fragments
        ],
        "seconds": elapsed,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "requests": len(results),
        "mean_token_recall": statistics.mean(r["recall"] for r in results),
        "complete_proportion": statistics.mean(r["complete"] for r in results),
        "mean_content_bytes": statistics.mean(r["content_bytes"] for r in results),
        "gold_selected": sum(r.get("gold_selected", False) for r in results),
        "recall_given_gold_selected": statistics.mean(r["recall"] for r in results if r.get("gold_selected"))
        if any(r.get("gold_selected") for r in results)
        else 0.0,
        "p50_seconds": statistics.median(r["seconds"] for r in results),
        "p95_seconds": sorted(r["seconds"] for r in results)[min(len(results) - 1, int(0.95 * len(results)))],
    }


def selection_metadata() -> dict[str, Any]:
    sources = {"prepared_evidence.py": hashlib.sha256(Path(prepared_evidence.__file__).read_bytes()).hexdigest()}
    parameters: dict[str, Any]
    if hasattr(prepared_evidence, "_DEFAULT_PROFILE"):
        matcher = sys.modules[prepared_evidence.EvidenceMatcher.__module__]
        matcher_file = matcher.__file__
        if matcher_file is None:
            raise RuntimeError("matcher-source-missing")
        sources["prepared_evidence_matching.py"] = hashlib.sha256(Path(matcher_file).read_bytes()).hexdigest()
        parameters = {"objective": "sentence_context", **asdict(prepared_evidence._DEFAULT_PROFILE)}
    else:
        parameters = {
            "objective": "global_weighted_tf",
            "k1": prepared_evidence._BM25_K1,
            "b": prepared_evidence._BM25_B,
            "tf_cap": prepared_evidence._TF_CAP,
            "document_prior_base": prepared_evidence._DOCUMENT_BASE,
        }
    parameters.update(window_limit=prepared_evidence._WINDOW_LIMIT, beam_width=prepared_evidence._BEAM_WIDTH)
    return {"selector_sha256": sources["prepared_evidence.py"], "source_hashes": sources, "parameters": parameters}


def make_report(raw: bytes, cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_sha256": hashlib.sha256(raw).hexdigest(),
        "claims": len(cases),
        **selection_metadata(),
        "summary": summarize(results),
        "by_budget": {str(b): summarize([r for r in results if r["budget"] == b]) for b in (1400, 1800)},
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = args.cases.read_bytes()
    cases = [json.loads(line) for line in data.splitlines() if line]
    if not cases:
        parser.error("cases must not be empty")
    results = []
    for i, case in enumerate(cases, 1):
        results.extend(evaluate_case(case, budget) for budget in (1400, 1800))
        if i % 25 == 0:
            print(f"evaluated {i}/{len(cases)} claims", flush=True)
    summary = summarize(results)
    report = make_report(data, cases, results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Prepare fixed public train/dev workloads with labels isolated from product code."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark.evidence_selection.evaluate import MEMORY, SCOPE, entry_version, merge_intervals
from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

EXPECTED = {
    "corpus.jsonl": "b8d6c89624cb2ed74dee8938effc4f5d8bd2086887880af8110d64be4ceade62",
    "claims_train.jsonl": "f4c8fa82d8bd0653a9cc8d61a6ea48c25eacea64e90af5dbf390ebb1b74372f0",
    "claims_dev.jsonl": "86f0435d08fdb65d1aa41d1472684f57e6e71930626497bdf4d7a9ec1a632217",
}


def load(root: Path, name: str) -> list[dict[str, Any]]:
    raw = (root / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED[name]:
        raise ValueError("public data hash mismatch")  # noqa: TRY003
    return [json.loads(line) for line in raw.splitlines()]


def gold_intervals(sentences: list[str], indexes: set[int]) -> list[tuple[int, int]]:
    intervals = []
    start = 0
    for i, sentence in enumerate(sentences):
        if i in indexes:
            intervals.append((start, start + len(sentence)))
        start += len(sentence) + 1
    return merge_intervals(intervals)


def oracle_bytes(doc_id: str, text: str, gold: list[tuple[int, int]]) -> int:
    """Minimum complete-gold JSON cost over continuous groups of gold intervals."""
    citation = {
        "scope_id": SCOPE,
        "memory_ref": MEMORY.model_dump(mode="json"),
        "entry_id": doc_id,
        "entry_version_id": entry_version(text),
    }
    empty = json.dumps({"trust": "untrusted_history", "items": []}, ensure_ascii=False, separators=(",", ":"))
    fixed = len(
        (
            TRUST_POLICY
            + "\n\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
            + empty
            + "\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"
        ).encode("utf-8")
    )
    costs = [0]
    for j in range(len(gold)):
        choices = []
        for i in range(j + 1):
            a, b = gold[i][0], gold[j][1]
            item = {"citation": citation, "content": text[a:b], "start": a, "end": b}
            choices.append(
                costs[i]
                + len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                + (1 if i else 0)
            )
        costs.append(min(choices))
    return fixed + costs[-1]


class Corpus:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.documents = {str(row["doc_id"]): row for row in rows}
        self.texts = {key: " ".join(row["abstract"]) for key, row in self.documents.items()}
        self.terms = {key: Counter(analyze_text(text).split()) for key, text in self.texts.items()}
        self.lengths = {key: sum(terms.values()) for key, terms in self.terms.items()}
        self.average = sum(self.lengths.values()) / len(self.lengths)
        self.df = Counter(term for terms in self.terms.values() for term in terms)

    def negatives(self, query: str, gold_id: str, excluded_ids: set[str] | None = None) -> list[str]:
        blocked = {gold_id} | (excluded_ids or set())
        terms = set(analyze_fts_query(query).split())
        weights = {t: 1 + math.log((len(self.texts) + 1) / (self.df[t] + 1)) for t in terms}
        scored = []
        for key, counts in self.terms.items():
            if key in blocked:
                continue
            norm = 1.2 * (0.25 + 0.75 * self.lengths[key] / self.average)
            score = sum(
                w * min(2, counts[t]) * 2.2 / (min(2, counts[t]) + norm) for t, w in weights.items() if t in counts
            )
            scored.append((-score, key))
        return [key for _, key in sorted(scored)[:2]]

    def case(self, claim: dict[str, Any], *, gold_id: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        if gold_id is None and len(claim["evidence"]) != 1:
            return None, "not_single_gold_document"
        doc_id = gold_id if gold_id is not None else next(iter(claim["evidence"]))
        indexes = {i for rationale in claim["evidence"][doc_id] for i in rationale["sentences"]}
        if not indexes:
            return None, "no_gold_intervals"
        gold = gold_intervals(self.documents[doc_id]["abstract"], indexes)
        minimum = oracle_bytes(doc_id, self.texts[doc_id], gold)
        if minimum > 1400:
            return None, "gold_does_not_fit_both_budgets"
        docs = [doc_id, *self.negatives(claim["claim"], doc_id, set(claim["evidence"]))]
        docs.sort(key=lambda key: hashlib.sha256((claim["claim"] + "\0" + key).encode()).digest())
        return {
            "id": claim["id"],
            "query": claim["claim"],
            "candidates": [{"entry_id": key, "text": self.texts[key]} for key in docs],
            "gold": {"entry_id": doc_id, "intervals": gold},
            "oracle_bytes": minimum,
        }, None


def prepare_supplementary(
    corpus: Corpus, claims: list[dict[str, Any]], previous: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Freeze unseen claims with complete gold-document separation before scoring."""
    old_ids = {case["id"] for case in previous}
    old_gold = {case["gold"]["entry_id"] for case in previous}
    queries = {case["query"].casefold().strip() for case in previous}
    cases = []
    excluded: Counter[str] = Counter()
    for claim in sorted(claims, key=lambda row: row["id"]):
        if claim["id"] in old_ids or len(claim["evidence"]) <= 1:
            continue
        if set(claim["evidence"]).intersection(old_gold):
            excluded["gold_document_shared_with_previous"] += 1
            continue
        query = claim["claim"].casefold().strip()
        if query in queries:
            excluded["duplicate_query"] += 1
            continue
        queries.add(query)
        case, reason = corpus.case(claim, gold_id=sorted(claim["evidence"])[0])
        if case is None:
            excluded[reason or "invalid_claim"] += 1
        else:
            case["supporting_doc_ids"] = sorted(claim["evidence"])
            cases.append(case)
    return cases, dict(excluded)


def write_supplementary(corpus: Corpus, claims: list[dict[str, Any]], previous_dir: Path, output: Path) -> None:
    previous = []
    hashes = {}
    for name in ("train", "validation"):
        raw = (previous_dir / f"{name}.jsonl").read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        previous.extend(json.loads(line) for line in raw.splitlines())
    cases, excluded = prepare_supplementary(corpus, claims, previous)
    data = "".join(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n" for case in cases)
    output.mkdir(parents=True, exist_ok=True)
    (output / "supplementary.jsonl").write_text(data, encoding="utf-8")
    source = Path(__file__).resolve().parents[2] / "src/powercontext/builtin/runtime/prepared_evidence.py"
    manifest = {
        "source_hashes": EXPECTED,
        "previous_case_hashes": hashes,
        "claims": len(cases),
        "requests": 2 * len(cases),
        "sha256": hashlib.sha256(data.encode()).hexdigest(),
        "selector_before_changes_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "excluded": excluded,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--supplementary", action="store_true")
    parser.add_argument("--previous-cases-dir", type=Path)
    args = parser.parse_args()
    if args.supplementary and args.previous_cases_dir is None:
        parser.error("--supplementary requires --previous-cases-dir")
    corpus = Corpus(load(args.source_root, "corpus.jsonl"))
    train = load(args.source_root, "claims_train.jsonl")
    dev = load(args.source_root, "claims_dev.jsonl")
    if args.supplementary:
        write_supplementary(corpus, [*train, *dev], args.previous_cases_dir, args.output_dir)
        return
    dev_docs = {doc for claim in dev for doc in claim["evidence"]}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"source_hashes": EXPECTED, "splits": {}}
    for name, claims in (("train", train), ("validation", dev)):
        cases = []
        excluded = Counter()
        for claim in claims:
            if name == "train" and set(claim["evidence"]).intersection(dev_docs):
                excluded["gold_document_shared_with_dev"] += 1
                continue
            case, reason = corpus.case(claim)
            if case is None:
                excluded[reason or "invalid_claim"] += 1
            else:
                cases.append(case)
        data = "".join(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n" for case in cases)
        (args.output_dir / f"{name}.jsonl").write_text(data, encoding="utf-8")
        manifest["splits"][name] = {
            "claims": len(cases),
            "sha256": hashlib.sha256(data.encode()).hexdigest(),
            "excluded": dict(excluded),
        }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()

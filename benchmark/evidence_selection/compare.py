"""Pair frozen requests and group training claims without leaking labels to selection."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def group_folds(cases: Sequence[dict[str, Any]]) -> dict[int, int]:
    return {c["id"]: int(hashlib.sha256(c["gold"]["entry_id"].encode()).hexdigest(), 16) % 3 for c in cases}


def paired_results(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if before["case_sha256"] != after["case_sha256"]:
        raise ValueError("case-hash-mismatch")
    a = {(r["id"], r["budget"]): r for r in before["results"]}
    b = {(r["id"], r["budget"]): r for r in after["results"]}
    if not a or a.keys() != b.keys() or len(a) != len(before["results"]) or len(b) != len(after["results"]):
        raise ValueError("request-set-mismatch")
    changes = [
        {
            "id": key[0],
            "budget": key[1],
            "recall_delta": b[key]["recall"] - a[key]["recall"],
            "complete_before": a[key]["complete"],
            "complete_after": b[key]["complete"],
        }
        for key in a
    ]
    return {
        "requests": len(a),
        "recall_delta": statistics.mean(c["recall_delta"] for c in changes),
        "recall_improved": sum(c["recall_delta"] > 1e-12 for c in changes),
        "recall_regressed": sum(c["recall_delta"] < -1e-12 for c in changes),
        "complete_gained": sum(not c["complete_before"] and c["complete_after"] for c in changes),
        "complete_lost": sum(c["complete_before"] and not c["complete_after"] for c in changes),
        "changes": changes,
    }


def training_selection(reports: dict[str, dict[str, Any]], cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from benchmark.evidence_selection.evaluate import summarize

    baseline = reports["baseline"]
    for report in reports.values():
        paired_results(baseline, report)
    folds = group_folds(cases)

    def records(name: str, identifiers: set[int]) -> list[dict[str, Any]]:
        return [r for r in reports[name]["results"] if r["id"] in identifiers]

    def eligible(name: str, identifiers: set[int]) -> bool:
        before, after = records("baseline", identifiers), records(name, identifiers)
        for budget in (1400, 1800):
            a, b = ([r for r in rs if r["budget"] == budget] for rs in (before, after))
            if not a or any(
                statistics.mean(r[key] for r in b) + 1e-12 < statistics.mean(r[key] for r in a)
                for key in ("recall", "complete")
            ):
                return False
        return statistics.mean(r["recall"] for r in after) > statistics.mean(
            r["recall"] for r in before
        ) + 1e-12 and sum(r["gold_selected"] for r in after) >= sum(r["gold_selected"] for r in before)

    def choose(identifiers: set[int]) -> tuple[str, list[str]]:
        admitted = [name for name in sorted(reports) if name != "baseline" and eligible(name, identifiers)]

        def rank(name: str) -> tuple[float, float, int, int, str]:
            rs = records(name, identifiers)
            params = reports[name]["parameters"]
            complexity = sum(
                bool(params.get(k))
                for k in (
                    "sentence_prior",
                    "result_weight",
                    "context_weight",
                    "distant_result_weight",
                    "word_forms",
                    "abbreviations",
                )
            )
            return (
                -statistics.mean(r["recall"] for r in rs),
                -statistics.mean(r["complete"] for r in rs),
                -sum(r["gold_selected"] for r in rs),
                complexity,
                name,
            )

        return min(admitted, key=rank) if admitted else "baseline", admitted

    details = {}
    held_out = []
    for fold in (0, 1, 2):
        train_ids = {cid for cid, f in folds.items() if f != fold}
        selected, admitted = choose(train_ids)
        test_ids = {cid for cid, f in folds.items() if f == fold}
        rs = records(selected, test_ids)
        held_out.extend(rs)
        details[str(fold)] = {
            "selected": selected,
            "admitted": admitted,
            "training_claims": len(train_ids),
            "held_out_claims": len(test_ids),
            "summary": summarize(rs),
        }
    selected, admitted = choose(set(folds))
    return {
        "selected": selected if selected != "baseline" else None,
        "admitted": admitted,
        "folds": details,
        "cross_validation": summarize(held_out),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.cases.read_bytes()
    before, after = (json.loads(p.read_text()) for p in (args.before, args.after))
    if before["case_sha256"] != hashlib.sha256(raw).hexdigest():
        parser.error("cases do not match report")
    result = paired_results(before, after)
    result["folds"] = group_folds([json.loads(line) for line in raw.splitlines() if line])
    result["by_budget"] = {
        str(b): paired_results(
            {**before, "results": [r for r in before["results"] if r["budget"] == b]},
            {**after, "results": [r for r in after["results"] if r["budget"] == b]},
        )
        for b in sorted({r["budget"] for r in before["results"]})
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

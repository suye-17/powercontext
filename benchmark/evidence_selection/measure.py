"""Reproduce local Builder cost measurements without backend or model calls."""

from __future__ import annotations

import argparse
import cProfile
import gc
import hashlib
import json
import platform
import statistics
import time
import tracemalloc
from collections.abc import Sequence
from pathlib import Path
from types import CodeType
from typing import Any

from benchmark.evidence_selection.evaluate import MEMORY, SCOPE, entry_version, selection_metadata
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.artifacts.search import analyze_fts_query
from powercontext.builtin.runtime import PrepareContextRequest, prepared_evidence
from powercontext.builtin.runtime.prepared_context import (
    PreparedContextBuild,
    PreparedContextBuilder,
    PreparedMemoryCandidates,
)


def measure(name: str, texts: Sequence[str], query: str, budget: int, repeats: int) -> dict[str, Any]:
    hits = tuple(
        MemoryHit(
            memory_ref=MEMORY,
            entry_id=str(i),
            entry_version_id=entry_version(text),
            text=text,
            score=1.0,
            matched_by=("fts",),
        )
        for i, text in enumerate(texts)
    )
    groups = (PreparedMemoryCandidates(SCOPE, MEMORY, hits),)
    request = PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True)

    def build() -> PreparedContextBuild:
        return PreparedContextBuilder().build_scopes_result(
            current_scope_id=SCOPE, memory_candidates=groups, request=request
        )

    build()
    timings = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        build()
        timings.append(time.perf_counter() - start)
    gc.collect()
    tracemalloc.start()
    build()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    entries = prepared_evidence._entries(groups)
    selector_type: Any = prepared_evidence._Selection
    query_argument = query if hasattr(prepared_evidence, "_DEFAULT_PROFILE") else set(analyze_fts_query(query).split())
    selector = selector_type(entries, query_argument, budget)
    generated = sum(1 for entry in entries for _ in prepared_evidence._windows(entry, budget))
    retained = len(selector.pool())
    profile = cProfile.Profile()
    profile.runcall(build)
    attempts = sum(
        stat.callcount
        for stat in profile.getstats()
        if isinstance(stat.code, CodeType)
        and stat.code.co_filename.endswith("runtime/prepared_evidence.py")
        and stat.code.co_name == "extend"
    )
    return {
        "scenario": name,
        "entries": len(texts),
        "input_utf8_bytes": sum(len(text.encode()) for text in texts),
        "input_hashes": [entry_version(text) for text in texts],
        "query": query,
        "budget": budget,
        "warmup_runs": 1,
        "timing_runs": repeats,
        "timings_seconds": timings,
        "p50_seconds": statistics.median(timings),
        "p95_seconds": sorted(timings)[min(repeats - 1, int(0.95 * repeats))],
        "tracemalloc_runs": 1,
        "tracemalloc_peak_bytes": peak,
        "generated_windows_including_duplicates": generated,
        "retained_windows": retained,
        "extend_calls_including_omission_probe": attempts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=1800)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    raw = args.cases.read_bytes()
    first = json.loads(raw.splitlines()[0])
    scenarios = [
        ("three_abstracts", [doc["text"] for doc in first["candidates"]], first["query"]),
        (
            "sixteen_near_limit",
            [("Recovery evidence improves patient survival. " * 220)[:8192]] * 16,
            "recovery survival",
        ),
        ("dense_short_sentences", [("Recovery. " * 820)[:8192]] * 16, "recovery"),
        ("direct_long_tail", ["unrelated " * 3200 + "Recovery survival"], "recovery survival"),
    ]
    rows = []
    for name, texts, query in scenarios:
        row = measure(name, texts, query, args.budget, args.repeats)
        rows.append(row)
        print(
            json.dumps({
                key: row[key]
                for key in ("scenario", "input_utf8_bytes", "p50_seconds", "p95_seconds", "tracemalloc_peak_bytes")
            }),
            flush=True,
        )
    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        **selection_metadata(),
        "measure_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "case_file_sha256": hashlib.sha256(raw).hexdigest(),
        "first_case_id": first["id"],
        "templates": {
            "sixteen_near_limit": "16 copies of ('Recovery evidence improves patient survival. '*220)[:8192]",
            "dense_short_sentences": "16 copies of ('Recovery. '*820)[:8192]",
            "direct_long_tail": "'unrelated '*3200+'Recovery survival'",
        },
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paperdesk.eval import EvalQuery, Expectation, evaluate, load_queries


def test_load_queries_accepts_object_and_plain_strings(tmp_path: Path) -> None:
    path = tmp_path / "queries.json"
    path.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query": "hybrid fusion",
                        "expected": ["rag-survey", {"paper_id": "rerank-notes", "section": "Method"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    queries = load_queries(path)

    assert len(queries) == 1
    assert queries[0].expected == (
        Expectation(paper_id="rag-survey"),
        Expectation(paper_id="rerank-notes", section="Method"),
    )


def test_load_queries_rejects_empty_expectations(tmp_path: Path) -> None:
    path = tmp_path / "queries.json"
    path.write_text(json.dumps([{"query": "x", "expected": []}]), encoding="utf-8")

    with pytest.raises(ValueError, match="expected results"):
        load_queries(path)


def test_evaluate_reports_rank_and_mrr(store, library) -> None:
    store.replace_all(library)
    queries = [
        EvalQuery("small chunks lose context", (Expectation("chunking-notes", "Limitations"),)),
        EvalQuery("hybrid fusion", (Expectation("rag-survey", "Method"), Expectation("rerank-notes", "Method"))),
    ]

    report = evaluate(store, queries)

    assert report["query_count"] == 2
    assert report["recall@1"] == 1.0
    assert report["recall@5"] == 1.0
    assert report["mrr"] == 1.0
    assert all(item["first_relevant_rank"] == 1 for item in report["details"])


def test_evaluate_honors_filters(store, library) -> None:
    store.replace_all(library)
    queries = [
        EvalQuery(
            "hybrid fusion retrieval",
            (Expectation("rag-survey", "Method"),),
            year_min=2023,
        )
    ]

    report = evaluate(store, queries)

    assert report["recall@3"] == 1.0
    assert report["details"][0]["first_relevant_rank"] == 2
    top_ids = [item["paper_id"] for item in report["details"][0]["top"]]
    assert "rerank-notes" in top_ids
    assert "chunking-notes" not in top_ids


def test_evaluate_marks_misses(store, library) -> None:
    store.replace_all(library)
    queries = [EvalQuery("quantum chromodynamics lattice", (Expectation("missing-paper"),))]

    report = evaluate(store, queries)

    assert report["recall@1"] == 0.0
    assert report["mrr"] == 0.0
    assert report["details"][0]["first_relevant_rank"] is None

"""Regression checks for the independent evidence quality scorer."""

from typing import Any

import pytest


def test_metrics_merge_overlap_and_count_repeated_words_once() -> None:
    from benchmark.evidence_selection.evaluate import score_intervals

    text = "alpha alpha beta gamma"
    recall, complete = score_intervals(text, [(0, len(text))], [(0, 11), (0, 11), (6, 16)])
    assert recall == pytest.approx(3 / 4)
    assert complete is False


def test_metrics_require_all_gold_ranges_and_merge_adjacent_selections() -> None:
    from benchmark.evidence_selection.evaluate import score_intervals

    text = "alpha unused beta"
    assert score_intervals(text, [(0, 5), (13, 17)], [(0, 2), (2, 5), (13, 17)]) == (1.0, True)
    assert score_intervals(text, [(0, 5), (13, 17)], [(0, 5)]) == (0.5, False)


def test_metrics_token_recall_is_a_multiset_not_a_set() -> None:
    from benchmark.evidence_selection.evaluate import score_intervals

    text = "alpha alpha alpha beta"
    assert score_intervals(text, [(0, len(text))], [(0, 5)]) == (0.25, False)


def test_evaluation_does_not_credit_non_gold_documents() -> None:
    from benchmark.evidence_selection.evaluate import evaluate_case

    case = {
        "id": "example",
        "query": "beta",
        "candidates": [
            {"entry_id": "gold", "text": "alpha evidence"},
            {"entry_id": "other", "text": "beta alpha evidence"},
            {"entry_id": "noise", "text": "unrelated"},
        ],
        "gold": {"entry_id": "gold", "intervals": [[0, 14]]},
    }
    result = evaluate_case(case, 1800)
    assert result["recall"] == 0
    assert result["complete"] is False


def test_supplementary_cases_exclude_all_supporting_documents_from_negatives() -> None:
    from benchmark.evidence_selection.prepare import Corpus, prepare_supplementary

    corpus = Corpus([
        {"doc_id": i, "abstract": [text]}
        for i, text in [
            (1, "Previous evidence"),
            (2, "Alpha recovery"),
            (3, "Alpha recovery second supporting evidence"),
            (4, "Alpha recovery observation"),
            (5, "Other unrelated observation"),
        ]
    ])
    claims = [{"id": 10, "claim": "Alpha recovery", "evidence": {"2": [{"sentences": [0]}], "3": [{"sentences": [0]}]}}]
    previous = [{"id": 1, "query": "old query", "gold": {"entry_id": "1"}}]
    cases, excluded = prepare_supplementary(corpus, claims, previous)
    assert len(cases) == 1 and not excluded
    assert cases[0]["gold"] == {"entry_id": "2", "intervals": [(0, len("Alpha recovery"))]}
    identifiers = {doc["entry_id"] for doc in cases[0]["candidates"]}
    assert len(identifiers) == 3 and "2" in identifiers and "3" not in identifiers


def test_supplementary_claim_with_any_previous_gold_document_is_excluded() -> None:
    from benchmark.evidence_selection.prepare import Corpus, prepare_supplementary

    corpus = Corpus([{"doc_id": i, "abstract": ["Alpha recovery"]} for i in range(1, 6)])
    claims = [{"id": 10, "claim": "Alpha recovery", "evidence": {"1": [{"sentences": [0]}], "2": [{"sentences": [0]}]}}]
    previous = [{"id": 1, "query": "old query", "gold": {"entry_id": "1"}}]
    cases, excluded = prepare_supplementary(corpus, claims, previous)
    assert cases == []
    assert excluded == {"gold_document_shared_with_previous": 1}


def test_supplementary_query_deduplication_keeps_first_claim_before_budget_filtering() -> None:
    from benchmark.evidence_selection.prepare import Corpus, prepare_supplementary

    corpus = Corpus([{"doc_id": i, "abstract": ["Alpha recovery"]} for i in range(1, 6)])
    evidence = {"2": [{"sentences": [0]}], "3": [{"sentences": [0]}]}
    claims = [
        {"id": 20, "claim": "ALPHA RECOVERY ", "evidence": evidence},
        {"id": 10, "claim": "Alpha recovery", "evidence": evidence},
    ]
    cases, excluded = prepare_supplementary(corpus, claims, [])
    assert [case["id"] for case in cases] == [10]
    assert excluded == {"duplicate_query": 1}


def test_comparison_folds_keep_gold_documents_together() -> None:
    from benchmark.evidence_selection.compare import group_folds

    cases = [
        {"id": 1, "gold": {"entry_id": "same"}},
        {"id": 2, "gold": {"entry_id": "same"}},
        {"id": 3, "gold": {"entry_id": "other"}},
    ]
    folds = group_folds(cases)
    assert folds[1] == folds[2]
    assert set(folds) == {1, 2, 3}
    assert all(fold in (0, 1, 2) for fold in folds.values())


@pytest.mark.parametrize("mismatch", ["case_sha256", "budget"])
def test_comparison_rejects_different_workloads(mismatch: str) -> None:
    from benchmark.evidence_selection.compare import paired_results

    before = {"case_sha256": "a", "results": [{"id": 1, "budget": 1400, "recall": 0.5, "complete": False}]}
    after = {
        "case_sha256": "b" if mismatch == "case_sha256" else "a",
        "results": [{"id": 1, "budget": 1800 if mismatch == "budget" else 1400, "recall": 1.0, "complete": True}],
    }
    with pytest.raises(ValueError):
        paired_results(before, after)


def test_comparison_pairs_by_request_and_reports_regression() -> None:
    from benchmark.evidence_selection.compare import paired_results

    before = {
        "case_sha256": "a",
        "results": [
            {"id": 1, "budget": 1400, "recall": 0.5, "complete": False},
            {"id": 1, "budget": 1800, "recall": 1.0, "complete": True},
        ],
    }
    after = {
        "case_sha256": "a",
        "results": [
            {"id": 1, "budget": 1800, "recall": 0.5, "complete": False},
            {"id": 1, "budget": 1400, "recall": 1.0, "complete": True},
        ],
    }
    result = paired_results(before, after)
    assert result["recall_delta"] == 0
    assert result["recall_improved"] == result["recall_regressed"] == 1
    assert result["complete_gained"] == result["complete_lost"] == 1


def test_training_selection_excludes_held_out_document_fold() -> None:
    from benchmark.evidence_selection.compare import group_folds, training_selection

    candidates: list[dict[str, Any]] = [{"id": i, "gold": {"entry_id": str(i)}} for i in range(30)]
    assignment = group_folds(candidates)
    cases = [next(c for c in candidates if assignment[c["id"]] == fold) for fold in range(3)]
    reports = {}
    for name, values in [("baseline", [0.0, 0.0, 0.0]), ("x", [1.0, 0.2, 0.2]), ("y", [0.1, 0.9, 0.9])]:
        reports[name] = {
            "case_sha256": "fixed",
            "parameters": {},
            "results": [
                {
                    "id": case["id"],
                    "budget": budget,
                    "recall": values[fold],
                    "complete": False,
                    "gold_selected": True,
                    "content_bytes": 800,
                    "seconds": 0.0,
                }
                for fold, case in enumerate(cases)
                for budget in (1400, 1800)
            ],
        }
    selection = training_selection(reports, cases)
    assert selection["folds"]["0"]["selected"] == "y"
    assert selection["folds"]["1"]["selected"] == "x"
    assert selection["selected"] == "y"
    assert selection["cross_validation"]["mean_token_recall"] == pytest.approx(1 / 6)

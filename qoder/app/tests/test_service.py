from __future__ import annotations

from datetime import datetime, timezone

import pytest

from paperdesk.llm import LLMConfig
from paperdesk.models import Goal, Profile, SessionSummary
from paperdesk.service import PaperDeskService, make_progress, new_session_id
from paperdesk.store import PaperStore

WHEN = datetime(2026, 10, 24, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def desk(store: PaperStore, library) -> PaperDeskService:
    store.replace_all(library)
    return PaperDeskService(store, llm_config=LLMConfig(mode="extractive"))


def _goal() -> Goal:
    return Goal(
        goal_id="goal-rag",
        text="本周读完 RAG 相关两篇，并整理 rerank 要点",
        tags=("rag", "rerank"),
        deadline=None,
    )


def test_plan_prefers_in_progress_paper(desk: PaperDeskService) -> None:
    progress = make_progress(
        "rag-survey",
        status="reading",
        last_section="Method",
        next_action="Evaluation",
        updated_at=WHEN,
    )

    items = desk.plan(_goal(), progress=[progress], limit=3)

    assert items[0].paper_id == "rag-survey"
    assert items[0].status == "reading"
    assert items[0].next_section == "Evaluation"
    assert any("命中目标标签：rag" in reason for reason in items[0].reasons)
    assert any("上次读到 Method" in reason for reason in items[0].reasons)
    assert any("下一步：Evaluation" in reason for reason in items[0].reasons)


def test_plan_puts_finished_papers_last(desk: PaperDeskService) -> None:
    items = desk.plan(_goal(), limit=3)

    assert [item.paper_id for item in items][-1] == "rag-survey"
    assert items[-1].status == "done"
    assert any("已完成" in reason for reason in items[-1].reasons)


def test_continue_card_uses_latest_reading_progress(desk: PaperDeskService) -> None:
    progress = make_progress(
        "rag-survey",
        status="reading",
        last_section="Method",
        next_action="Evaluation",
        updated_at=WHEN,
    )
    session = SessionSummary(
        session_id="session-1",
        read_papers=("rag-survey",),
        questions=("rerank 在 RAG 里解决什么问题？",),
        next_steps=("读完 Evaluation",),
        created_at=WHEN.isoformat(timespec="seconds"),
    )

    card = desk.continue_card(goal=_goal(), progress=[progress], session=session)

    assert card.paper_id == "rag-survey"
    assert "继续上次" in card.headline
    assert "Retrieval Augmented Generation" in card.headline
    assert card.last_section == "Method"
    assert card.next_action == "Evaluation"
    assert card.questions == ("rerank 在 RAG 里解决什么问题？",)
    assert "速读 3 条要点" in card.profile_summary


def test_continue_card_without_progress_starts_a_paper(desk: PaperDeskService) -> None:
    card = desk.continue_card(profile=Profile(digest_sentences=5))

    assert card.paper_id == "rerank-notes"
    assert "开始" in card.headline
    assert "速读 5 条要点" in card.profile_summary


def test_continue_card_ignores_progress_for_missing_paper(desk: PaperDeskService) -> None:
    stale = make_progress(
        "deleted-paper",
        status="reading",
        last_section="Method",
        updated_at=WHEN,
    )

    card = desk.continue_card(progress=[stale])

    assert card.paper_id == "rerank-notes"
    assert "开始" in card.headline


def test_finish_session_deduplicates_and_keeps_order(desk: PaperDeskService) -> None:
    first = make_progress("rag-survey", status="reading", last_section="Method", updated_at=WHEN)
    second = make_progress("rerank-notes", status="reading", last_section="Abstract", updated_at=WHEN)

    summary = desk.finish_session(
        "session-1",
        updates=[first, second, first],
        questions=["rerank 有什么用？", " rerank 有什么用？ ", "RAG 的评估指标？"],
        next_steps=["读完 Evaluation"],
        created_at=WHEN,
    )

    assert summary.read_papers == ("rag-survey", "rerank-notes")
    assert summary.questions == ("rerank 有什么用？", "RAG 的评估指标？")
    assert summary.next_steps == ("读完 Evaluation",)
    assert summary.created_at == "2026-10-24T09:30:00+00:00"


def test_digest_uses_extractive_fallback_with_citations(desk: PaperDeskService) -> None:
    digest = desk.digest("rerank-notes")

    assert digest is not None
    assert digest.paper.title == "Reranking for Retrieval Pipelines"
    assert len(digest.chunks) == 3
    assert digest.synthesis.mode == "extractive"
    assert "[C1]" in digest.synthesis.text
    assert [citation.section for citation in digest.synthesis.citations] == [
        chunk.section for chunk in digest.chunks
    ]
    assert all(citation.paper_id == "rerank-notes" for citation in digest.synthesis.citations)


def test_answer_returns_hits_and_matching_citations(desk: PaperDeskService) -> None:
    answer = desk.answer("cross encoder", limit=3)

    assert answer.hits
    assert answer.synthesis.mode == "extractive"
    assert answer.synthesis.citations
    assert answer.synthesis.citations[0].chunk_id == answer.hits[0].chunk_id
    assert answer.synthesis.citations[0].start_line == answer.hits[0].start_line


def test_digest_of_unknown_paper_is_none(desk: PaperDeskService) -> None:
    assert desk.digest("missing-paper") is None


def test_make_progress_validates_status() -> None:
    with pytest.raises(ValueError, match="invalid status"):
        make_progress("rag-survey", status="paused")

    assert new_session_id(WHEN) == "session-20261024-093000"

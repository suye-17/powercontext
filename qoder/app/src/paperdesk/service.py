from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .llm import LLMClient, LLMConfig, SynthesisResult, synthesize
from .models import (
    VALID_STATUSES,
    Chunk,
    Goal,
    Paper,
    Profile,
    Progress,
    SearchHit,
    SessionSummary,
)
from .store import PaperStore


@dataclass(frozen=True)
class PlanItem:
    paper_id: str
    title: str
    status: str
    score: float
    next_section: str
    tags: tuple[str, ...]
    reasons: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "status": self.status,
            "score": self.score,
            "next_section": self.next_section,
            "tags": list(self.tags),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ContinueCard:
    paper_id: str | None
    title: str | None
    last_section: str | None
    next_action: str | None
    headline: str
    goal_text: str | None
    questions: tuple[str, ...]
    next_steps: tuple[str, ...]
    profile_summary: str

    def as_json(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "last_section": self.last_section,
            "next_action": self.next_action,
            "headline": self.headline,
            "goal_text": self.goal_text,
            "questions": list(self.questions),
            "next_steps": list(self.next_steps),
            "profile_summary": self.profile_summary,
        }


@dataclass(frozen=True)
class Answer:
    question: str
    hits: tuple[SearchHit, ...]
    synthesis: SynthesisResult

    def as_json(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "hits": [_hit_json(hit) for hit in self.hits],
            "synthesis": self.synthesis.as_json(),
        }


@dataclass(frozen=True)
class PaperDigest:
    paper: Paper
    chunks: tuple[Chunk, ...]
    synthesis: SynthesisResult

    def as_json(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper.paper_id,
            "title": self.paper.title,
            "chunks": [chunk.section for chunk in self.chunks],
            "synthesis": self.synthesis.as_json(),
        }


class PaperDeskService:
    def __init__(
        self,
        store: PaperStore,
        *,
        llm_config: LLMConfig | None = None,
        llm_client: LLMClient | None = None,
        embed_query: Callable[[str], Sequence[float] | None] | None = None,
    ) -> None:
        self.store = store
        self.llm_config = llm_config or LLMConfig.from_env()
        self.llm_client = llm_client
        self.embed_query = embed_query

    def plan(
        self,
        goal: Goal,
        *,
        profile: Profile = Profile(),
        progress: Mapping[str, Progress] | Sequence[Progress] = (),
        limit: int = 6,
    ) -> list[PlanItem]:
        current = _progress_map(progress)
        relevance = _paper_relevance(self._search(goal.text, limit=30))
        peak = max(relevance.values(), default=0.0)
        goal_tags = {tag.lower() for tag in goal.tags}
        items: list[PlanItem] = []
        for paper in self.store.list_papers():
            entry = current.get(paper.paper_id)
            effective_status = entry.status if entry is not None else paper.status
            score = 0.0
            reasons: list[str] = []
            if effective_status == "reading":
                score += 3.0
                reasons.append("已经开始阅读，适合继续推进")
            elif effective_status == "unread":
                score += 2.0
                reasons.append("尚未开始，适合按计划推进")
            else:
                score -= 1.0
                reasons.append("已完成，可用于回顾")

            overlap = sorted(goal_tags & {tag.lower() for tag in paper.tags})
            if overlap:
                score += min(2.0 * len(overlap), 4.0)
                reasons.append("命中目标标签：" + "、".join(overlap))

            if peak > 0 and paper.paper_id in relevance:
                relative = relevance[paper.paper_id] / peak
                score += 2.0 * relative
                reasons.append("与目标检索最相关" if relative >= 0.95 else "与目标关键词相关")

            if goal.deadline:
                score += 0.2
                reasons.append(f"目标截止：{goal.deadline}")

            next_section = ""
            if entry is not None and entry.status == "reading":
                score += 3.5
                reasons.append("记忆中有未完成的阅读进度")
            if entry is not None:
                if entry.last_section:
                    reasons.append(f"上次读到 {entry.last_section}")
                if entry.next_action:
                    score += 0.5
                    reasons.append(f"下一步：{entry.next_action}")
                    next_section = entry.next_action
                elif entry.last_section:
                    next_section = entry.last_section
            if not next_section:
                next_section = _following_section(
                    self.store.get_chunks(paper.paper_id),
                    entry.last_section if entry else "",
                )

            items.append(
                PlanItem(
                    paper_id=paper.paper_id,
                    title=paper.title,
                    status=effective_status,
                    score=round(score, 3),
                    next_section=next_section,
                    tags=paper.tags,
                    reasons=tuple(reasons),
                )
            )

        order = {"reading": 0, "unread": 1, "done": 2}
        items.sort(key=lambda item: (-item.score, order[item.status], item.paper_id))
        return items[: max(1, limit)]

    def continue_card(
        self,
        *,
        goal: Goal | None = None,
        profile: Profile = Profile(),
        progress: Mapping[str, Progress] | Sequence[Progress] = (),
        session: SessionSummary | None = None,
    ) -> ContinueCard:
        current = list(_progress_map(progress).values())
        reading = [item for item in current if item.status == "reading"]
        pool = reading or current
        pool.sort(key=lambda item: item.updated_at, reverse=True)
        entry = pool[0] if pool else None
        paper = self.store.get_paper(entry.paper_id) if entry else None
        if paper is None:
            papers = self.store.list_papers()
            paper = (
                next((item for item in papers if item.status == "reading"), None)
                or next((item for item in papers if item.status != "done"), None)
                or (papers[0] if papers else None)
            )
            entry = None

        questions = session.questions if session else ()
        next_steps = session.next_steps if session else ()
        if paper is None:
            headline = "资料库还是空的，先导入论文笔记。"
        elif entry is not None:
            headline = f"继续上次：{paper.title} · {entry.last_section or '新一章'}"
        else:
            headline = f"从《{paper.title}》开始"

        return ContinueCard(
            paper_id=paper.paper_id if paper else None,
            title=paper.title if paper else None,
            last_section=entry.last_section if entry else None,
            next_action=entry.next_action if entry else None,
            headline=headline,
            goal_text=goal.text if goal else None,
            questions=tuple(questions),
            next_steps=tuple(next_steps),
            profile_summary=(
                f"界面语言 {profile.language}，速读 {profile.digest_sentences} 条要点，"
                f"每周计划 {profile.weekly_minutes} 分钟"
            ),
        )

    def answer(
        self,
        question: str,
        *,
        profile: Profile = Profile(),
        tag: str | None = None,
        year_min: int | None = None,
        status: str | None = None,
        limit: int = 6,
    ) -> Answer:
        hits = self._search(
            question,
            limit=limit,
            tag=tag,
            year_min=year_min,
            status=status,
        )
        synthesis = synthesize(
            question,
            hits,
            profile=profile,
            config=self.llm_config,
            client=self.llm_client,
        )
        return Answer(question=question, hits=tuple(hits), synthesis=synthesis)

    def digest(self, paper_id: str, *, profile: Profile = Profile()) -> PaperDigest | None:
        paper = self.store.get_paper(paper_id)
        if paper is None:
            return None
        chunks = self.store.get_chunks(paper_id)
        if not chunks:
            return None
        limit = max(1, min(profile.digest_sentences + 2, len(chunks)))
        selected = chunks[:limit]
        hits = tuple(
            SearchHit(
                chunk_id=chunk.id,
                paper_id=paper.paper_id,
                title=paper.title,
                section=chunk.section,
                path=paper.path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                text=chunk.text,
                score=0.0,
                tags=paper.tags,
            )
            for chunk in selected
        )
        question = f"总结《{paper.title}》的研究问题、方法与主要结论"
        synthesis = synthesize(
            question,
            hits,
            profile=profile,
            config=self.llm_config,
            client=self.llm_client,
        )
        return PaperDigest(paper=paper, chunks=selected, synthesis=synthesis)

    def _search(self, query: str, **kwargs: Any) -> list[SearchHit]:
        embedding = self.embed_query(query) if self.embed_query is not None else None
        return self.store.search(query, query_embedding=embedding, **kwargs)

    def finish_session(
        self,
        session_id: str,
        *,
        updates: Iterable[Progress],
        questions: Iterable[str] = (),
        next_steps: Iterable[str] = (),
        created_at: datetime | None = None,
    ) -> SessionSummary:
        updates = tuple(updates)
        read_papers = tuple(dict.fromkeys(item.paper_id for item in updates))
        return SessionSummary(
            session_id=session_id,
            read_papers=read_papers,
            questions=tuple(dict.fromkeys(question.strip() for question in questions if question.strip())),
            next_steps=tuple(dict.fromkeys(step.strip() for step in next_steps if step.strip())),
            created_at=(created_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        )


def make_progress(
    paper_id: str,
    *,
    status: str,
    last_section: str = "",
    next_action: str = "",
    updated_at: datetime | None = None,
) -> Progress:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    return Progress(
        paper_id=paper_id,
        status=status,
        last_section=last_section.strip(),
        next_action=next_action.strip(),
        updated_at=(updated_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    )


def new_session_id(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return "session-" + moment.strftime("%Y%m%d-%H%M%S")


def _progress_map(
    progress: Mapping[str, Progress] | Sequence[Progress],
) -> dict[str, Progress]:
    if isinstance(progress, Mapping):
        return dict(progress)
    return {item.paper_id: item for item in progress}


def _paper_relevance(hits: Sequence[SearchHit]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for hit in hits:
        scores[hit.paper_id] = max(scores.get(hit.paper_id, 0.0), hit.score)
    return scores


def _following_section(chunks: Sequence[Chunk], last_section: str) -> str:
    if not chunks:
        return ""
    sections = [chunk.section for chunk in chunks]
    if last_section and last_section in sections:
        index = sections.index(last_section)
        if index + 1 < len(sections):
            return sections[index + 1]
    return sections[0]


def _hit_json(hit: SearchHit) -> dict[str, Any]:
    return {
        "chunk_id": hit.chunk_id,
        "paper_id": hit.paper_id,
        "title": hit.title,
        "section": hit.section,
        "path": hit.path,
        "start_line": hit.start_line,
        "end_line": hit.end_line,
        "text": hit.text,
        "score": hit.score,
        "fts_score": hit.fts_score,
        "vector_score": hit.vector_score,
        "tags": list(hit.tags),
    }

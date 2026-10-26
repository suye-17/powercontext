from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_STATUSES = ("unread", "reading", "done")


@dataclass(frozen=True)
class Paper:
    paper_id: str
    title: str
    authors: str
    year: int
    tags: tuple[str, ...]
    status: str
    path: str
    content: str
    content_hash: str
    updated_at: str


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    paper_id: str
    section: str
    order_index: int
    start_line: int
    end_line: int
    text: str
    searchable_text: str


@dataclass(frozen=True)
class ParsedPaper:
    paper: Paper
    chunks: tuple[Chunk, ...]


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    paper_id: str
    title: str
    section: str
    path: str
    start_line: int
    end_line: int
    text: str
    score: float
    vector_score: float | None = None
    fts_score: float = 0.0
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Profile:
    language: str = "zh"
    digest_sentences: int = 3
    citation_style: str = "inline"
    weekly_minutes: int = 120

    def as_json(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "digest_sentences": self.digest_sentences,
            "citation_style": self.citation_style,
            "weekly_minutes": self.weekly_minutes,
        }


@dataclass(frozen=True)
class Goal:
    goal_id: str
    text: str
    tags: tuple[str, ...]
    deadline: str | None
    status: str = "active"

    def as_json(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "text": self.text,
            "tags": list(self.tags),
            "deadline": self.deadline,
            "status": self.status,
        }


@dataclass(frozen=True)
class Progress:
    paper_id: str
    status: str
    last_section: str
    next_action: str
    updated_at: str

    def as_json(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "status": self.status,
            "last_section": self.last_section,
            "next_action": self.next_action,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    read_papers: tuple[str, ...]
    questions: tuple[str, ...]
    next_steps: tuple[str, ...]
    created_at: str

    def as_json(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "read_papers": list(self.read_papers),
            "questions": list(self.questions),
            "next_steps": list(self.next_steps),
            "created_at": self.created_at,
        }

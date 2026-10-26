from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Document:
    id: str
    path: str
    kind: str
    title: str
    content: str
    content_hash: str
    updated_at: str


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    kind: str
    path: str
    title: str
    text: str
    searchable_text: str
    start_line: int
    end_line: int
    spec_id: str | None = None
    symbol: str | None = None
    test_name: str | None = None

    def source(self) -> str:
        anchor = self.spec_id or self.symbol or self.test_name or f"L{self.start_line}"
        return f"{self.path}#{anchor}"

    def to_dict(self, score: float) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text,
            "score": round(score, 4),
        }
        for key in ("spec_id", "symbol", "test_name"):
            value = getattr(self, key)
            if value:
                payload[key] = value
        return payload


@dataclass
class ContextPack:
    task: str
    specs: list[dict[str, Any]] = field(default_factory=list)
    code: list[dict[str, Any]] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "specs": self.specs,
            "code": self.code,
            "tests": self.tests,
            "state": self.state,
            "sources": self.sources,
        }

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import SearchHit
from .store import PaperStore

KS = (1, 3, 5)


@dataclass(frozen=True)
class Expectation:
    paper_id: str
    section: str | None = None

    def matches(self, hit: SearchHit) -> bool:
        if hit.paper_id != self.paper_id:
            return False
        if self.section is None:
            return True
        return hit.section.strip().casefold() == self.section.strip().casefold()


@dataclass(frozen=True)
class EvalQuery:
    query: str
    expected: tuple[Expectation, ...]
    tag: str | None = None
    year_min: int | None = None
    status: str | None = None
    note: str = ""


def load_queries(path: Path | str) -> list[EvalQuery]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("queries") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path}: expected a non-empty list of queries")
    queries: list[EvalQuery] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not str(item.get("query", "")).strip():
            raise ValueError(f"{path}: query #{index} is missing a query string")
        raw_expected = item.get("expected")
        if not isinstance(raw_expected, list) or not raw_expected:
            raise ValueError(f"{path}: query #{index} is missing expected results")
        expected: list[Expectation] = []
        for raw in raw_expected:
            if isinstance(raw, str):
                expected.append(Expectation(paper_id=raw))
                continue
            if not isinstance(raw, dict) or not raw.get("paper_id"):
                raise ValueError(f"{path}: query #{index} has an invalid expected entry")
            section = raw.get("section")
            expected.append(Expectation(paper_id=str(raw["paper_id"]), section=str(section) if section else None))
        queries.append(
            EvalQuery(
                query=str(item["query"]),
                expected=tuple(expected),
                tag=_optional(item.get("tag")),
                year_min=int(item["year_min"]) if item.get("year_min") is not None else None,
                status=_optional(item.get("status")),
                note=str(item.get("note", "")),
            )
        )
    return queries


def evaluate(
    store: PaperStore,
    queries: list[EvalQuery],
    *,
    query_embeddings: dict[str, tuple[float, ...]] | None = None,
) -> dict[str, Any]:
    if not queries:
        raise ValueError("at least one query is required")
    details: list[dict[str, Any]] = []
    reciprocal_sum = 0.0
    hits_at = {k: 0 for k in KS}
    for item in queries:
        embedding = query_embeddings.get(item.query) if query_embeddings else None
        results = store.search(
            item.query,
            limit=max(KS),
            tag=item.tag,
            year_min=item.year_min,
            status=item.status,
            query_embedding=embedding,
        )
        rank = _first_rank(results, item.expected)
        if rank is not None:
            reciprocal_sum += 1.0 / rank
            for k in KS:
                if rank <= k:
                    hits_at[k] += 1
        details.append(
            {
                "query": item.query,
                "filters": {"tag": item.tag, "year_min": item.year_min, "status": item.status},
                "first_relevant_rank": rank,
                "top": [_top(hit) for hit in results[: max(KS)]],
                "note": item.note,
            }
        )
    total = len(queries)
    return {
        "query_count": total,
        "recall@1": round(hits_at[1] / total, 4),
        "recall@3": round(hits_at[3] / total, 4),
        "recall@5": round(hits_at[5] / total, 4),
        "mrr": round(reciprocal_sum / total, 4),
        "details": details,
    }


def _first_rank(hits: list[SearchHit], expected: tuple[Expectation, ...]) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if any(expectation.matches(hit) for expectation in expected):
            return rank
    return None


def _top(hit: SearchHit) -> dict[str, Any]:
    return {
        "paper_id": hit.paper_id,
        "section": hit.section,
        "score": round(hit.score, 4),
        "fts_score": round(hit.fts_score, 4),
        "vector_score": round(hit.vector_score, 4) if hit.vector_score is not None else None,
        "path": hit.path,
        "start_line": hit.start_line,
        "end_line": hit.end_line,
    }


def _optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

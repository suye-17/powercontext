"""HTTP scoring adapter for Memory reranking."""

from __future__ import annotations

import math
from typing import cast

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score coarse Memory candidates through a caller-owned HTTP client."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Select original candidate ranks using scores from every batch."""

        if not candidates:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= len(candidates):
            raise ValueError("limit must be between one and the candidate count")  # noqa: TRY003
        numbered = [{"rank": rank, "text": candidate.text} for rank, candidate in enumerate(candidates, start=1)]
        scores: dict[int, int | float] = {}
        requests = 0
        for start in range(0, len(numbered), self._batch_size):
            batch = numbered[start : start + self._batch_size]
            response = await self._client.post(self._endpoint, json={"query": query, "candidates": batch})
            requests += 1
            response.raise_for_status()
            expected_ranks = set(range(start + 1, start + len(batch) + 1))
            scores.update(_validated_batch_scores(response.json(), expected_ranks))
        selected = tuple(rank for rank, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit])
        return MemoryRerankDecision(selected_ranks=selected, usage=InferenceUsage(requests=requests))


def _validated_batch_scores(payload: object, expected_ranks: set[int]) -> dict[int, int | float]:
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("response must contain only scores")  # noqa: TRY003
    items = cast("dict[str, object]", payload)["scores"]
    if not isinstance(items, list) or len(items) != len(expected_ranks):
        raise ValueError("response scores must cover this batch")  # noqa: TRY003
    scores: dict[int, int | float] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"rank", "score"}:
            raise ValueError("score item must contain only rank and score")  # noqa: TRY003
        score_item = cast("dict[str, object]", item)
        rank = score_item["rank"]
        score = score_item["score"]
        if not isinstance(rank, int) or isinstance(rank, bool) or rank not in expected_ranks or rank in scores:
            raise ValueError("response rank must appear once in its batch")  # noqa: TRY003
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError("score must be a finite number")  # noqa: TRY003, TRY004
        if isinstance(score, float) and not math.isfinite(score):
            raise ValueError("score must be a finite number")  # noqa: TRY003
        scores[rank] = score
    return scores

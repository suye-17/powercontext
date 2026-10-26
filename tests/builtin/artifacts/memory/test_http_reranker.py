"""Observable HTTP Memory reranking behavior."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker


def _hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id=f"entry-{rank}",
        entry_version_id=f"entry-{rank}-v1",
        text=f"Candidate {rank}",
        score=1 / rank,
        matched_by=("fts",),
    )


def test_http_reranker_sorts_all_batches_by_score_and_original_rank() -> None:
    requests: list[dict[str, Any]] = []
    scores = {1: 0.1, 2: 0.9, 3: 0.95, 4: 0.9, 5: 0.2}

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://rerank.example/score"
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "scores": [
                    {"rank": candidate["rank"], "score": scores[candidate["rank"]]}
                    for candidate in reversed(body["candidates"])
                ]
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            decision = await HTTPMemoryReranker("https://rerank.example/score", client=client, batch_size=2).rerank(
                "Which candidate matters?", tuple(_hit(rank) for rank in range(1, 6)), 3
            )
        assert decision.selected_ranks == (3, 2, 4)
        assert decision.usage.requests == 3
        assert decision.used_fallback is False
        assert len(requests) == 3
        assert all(request["query"] == "Which candidate matters?" for request in requests)
        assert all(len(request["candidates"]) <= 2 for request in requests)
        sent = [candidate for request in requests for candidate in request["candidates"]]
        assert sorted(sent, key=lambda candidate: candidate["rank"]) == [
            {"rank": rank, "text": f"Candidate {rank}"} for rank in range(1, 6)
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("batch_size", [0, -1, True])
def test_http_reranker_rejects_invalid_batch_size(batch_size: int) -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://rerank.example/score", client=client, batch_size=batch_size)

    asyncio.run(scenario())


@pytest.mark.parametrize("candidate_count,limit", [(0, 1), (2, 0), (2, 3), (2, True)])
def test_http_reranker_rejects_invalid_selection_before_request(candidate_count: int, limit: int) -> None:
    def unexpected_request(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid reranking input reached HTTP")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
            reranker = HTTPMemoryReranker("https://rerank.example/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, candidate_count + 1)), limit)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"scores": [], "extra": 1},
        {"scores": None},
        {"scores": {}},
        {"scores": [{"rank": 1, "score": 0.5}, {"rank": 1, "score": 0.7}]},
        {"scores": [{"rank": 1, "score": 0.5}]},
        {"scores": [{"rank": 1, "score": 0.5}, {"rank": 3, "score": 0.7}]},
        {"scores": [{"rank": True, "score": 0.5}, {"rank": 2, "score": 0.7}]},
        {"scores": [{"rank": 1.0, "score": 0.5}, {"rank": 2, "score": 0.7}]},
        {"scores": [{"rank": 1, "score": 0.5, "extra": 1}, {"rank": 2, "score": 0.7}]},
        {"scores": [{"rank": 1}, {"rank": 2, "score": 0.7}]},
        {"scores": [{"rank": 1, "score": True}, {"rank": 2, "score": 0.7}]},
        {"scores": [{"rank": 1, "score": "0.5"}, {"rank": 2, "score": 0.7}]},
    ],
)
def test_http_reranker_rejects_invalid_response_schema(payload: object) -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as client:
            reranker = HTTPMemoryReranker("https://rerank.example/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1), _hit(2)), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("score_literal", ["NaN", "Infinity", "-Infinity"])
def test_http_reranker_rejects_nonfinite_json_scores(score_literal: str) -> None:
    content = f'{{"scores":[{{"rank":1,"score":{score_literal}}}]}}'.encode()

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
        ) as client:
            reranker = HTTPMemoryReranker("https://rerank.example/score", client=client)
            with pytest.raises(ValueError, match="score must be a finite number"):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_http_reranker_rejects_invalid_json() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"{"))
        ) as client:
            reranker = HTTPMemoryReranker("https://rerank.example/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("status_code", [400, 503])
def test_http_reranker_preserves_http_status_errors(status_code: int) -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status_code, json={"scores": []}))
        ) as client:
            reranker = HTTPMemoryReranker("https://rerank.example/score", client=client)
            with pytest.raises(httpx.HTTPStatusError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_http_reranker_preserves_network_errors() -> None:
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream timed out", request=request)  # noqa: TRY003

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(time_out)) as client:
            reranker = HTTPMemoryReranker("https://rerank.example/score", client=client)
            with pytest.raises(httpx.ReadTimeout):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "scores",
    [
        (9_007_199_254_740_992, 9_007_199_254_740_993),
        (1, 10**400),
    ],
)
def test_http_reranker_preserves_order_of_finite_integer_scores(scores: tuple[int, int]) -> None:
    async def scenario() -> None:
        payload = {"scores": [{"rank": 1, "score": scores[0]}, {"rank": 2, "score": scores[1]}]}
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as client:
            decision = await HTTPMemoryReranker("https://rerank.example/score", client=client).rerank(
                "query", (_hit(1), _hit(2)), 1
            )
        assert decision.selected_ranks == (2,)

    asyncio.run(scenario())

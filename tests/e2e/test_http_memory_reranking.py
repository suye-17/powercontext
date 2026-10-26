"""HTTP reranking through a real scoped Memory search."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from powercontext.builtin.artifacts.memory import HTTPMemoryReranker, MemoryCitation, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    RememberMemoryRequest,
    RuntimeConfig,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft


def test_runtime_http_reranking_keeps_exact_memory_entry_versions(tmp_path: Path) -> None:
    scores = {1: 0.1, 2: 0.9, 3: 0.95, 4: 0.9}
    requested_ranks: list[int] = []

    def score_batch(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requested_ranks.extend(candidate["rank"] for candidate in payload["candidates"])
        return httpx.Response(
            200,
            json={
                "scores": [
                    {"rank": candidate["rank"], "score": scores[candidate["rank"]]}
                    for candidate in reversed(payload["candidates"])
                ]
            },
        )

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'http-rerank.db'}")
        config = BuiltinConfig(database=database, runtime=RuntimeConfig(memory_rerank_candidate_limit=4))
        async with httpx.AsyncClient(transport=httpx.MockTransport(score_batch)) as client:
            reranker = HTTPMemoryReranker(
                "https://rerank.example/score", client=client, batch_size=2, policy_id="test-http-v1"
            )
            async with open_builtin_runtime(config, memory_reranker=reranker) as runtime:
                assert runtime.scopes is not None
                scope = await runtime.scopes.create(
                    ScopeDraft(
                        title="HTTP rerank", summary="Verify exact Memory identities", idempotency_key="http-rerank"
                    )
                )
                memory = runtime.memory.for_scope(scope.scope_id)
                written = await memory.remember(
                    RememberMemoryRequest(
                        entries=tuple(
                            MemoryEntryInput(kind="fact", text=f"Project fact {rank}.") for rank in range(1, 5)
                        )
                    )
                )
                page = await memory.search(SearchMemoryRequest(query="project", mode="fts", limit=2))
                assert page.rerank is not None
                assert page.memory_ref == written.memory_ref
                assert page.rerank.policy_id == "test-http-v1"
                assert page.rerank.selected_ranks == (3, 2)
                assert page.rerank.usage.requests == 2
                assert len(page.rerank.candidate_hits) == 4
                assert page.hits == (page.rerank.candidate_hits[2], page.rerank.candidate_hits[1])
                assert sorted(requested_ranks) == [1, 2, 3, 4]

                listed = await memory.list()
                entries = {record.entry.entry_id: record.entry for record in listed.entries}
                for hit in page.hits:
                    record = await memory.get(
                        GetMemoryEntryRequest(
                            citation=MemoryCitation(
                                memory_ref=hit.memory_ref,
                                entry_id=hit.entry_id,
                                entry_version_id=hit.entry_version_id,
                            )
                        )
                    )
                    assert hit.memory_ref == page.memory_ref
                    assert hit.entry_version_id == entries[hit.entry_id].entry_version_id
                    assert record.entry.entry_id == hit.entry_id
                    assert record.entry.entry_version_id == hit.entry_version_id
                    assert record.entry.text == hit.text

    asyncio.run(scenario())

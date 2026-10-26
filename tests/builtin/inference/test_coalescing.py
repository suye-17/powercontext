"""Behavior tests for cross-call embedding coalescing and vector caching."""

from __future__ import annotations

import asyncio
import math
from typing import Any

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage, QueryEmbeddingModel
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts

PROFILE = EmbeddingProfile(profile_id="coalescing-tests", model="offline", dimension=2)


class RecordingEmbedding:
    profile = PROFILE

    def __init__(self, usage: InferenceUsage | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.usage = usage or InferenceUsage(requests=1)

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.calls.append(tuple(texts))
        return EmbeddingResult(
            vectors=tuple((float(len(text)), 1.0) for text in texts),
            usage=self.usage,
        )


def test_concurrent_calls_share_work_and_restore_order() -> None:
    async def scenario() -> None:
        base = RecordingEmbedding(InferenceUsage(requests=1, input_tokens=3))
        model = CoalescingEmbeddingModel(base, max_batch_size=2, coalesce_ms=20, cache_size=3)

        first, second = await asyncio.gather(
            model.embed(("alpha", "beta", "alpha")),
            model.embed(("beta", "gamma")),
        )

        assert first.vectors == ((5.0, 1.0), (4.0, 1.0), (5.0, 1.0))
        assert second.vectors == ((4.0, 1.0), (5.0, 1.0))
        assert sorted(text for call in base.calls for text in call) == ["alpha", "beta", "gamma"]
        assert all(len(call) <= 2 for call in base.calls)
        assert first.usage.requests == sum(bool(set(call) & {"alpha", "beta"}) for call in base.calls)
        assert second.usage.requests == sum(bool(set(call) & {"beta", "gamma"}) for call in base.calls)
        assert first.usage.input_tokens == first.usage.requests * 3

        assert (await model.embed(("alpha",))).vectors == ((5.0, 1.0),)
        assert sum(call.count("alpha") for call in base.calls) == 1

    asyncio.run(scenario())


def test_empty_input_and_cache_hits_skip_base() -> None:
    async def scenario() -> None:
        base = RecordingEmbedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=2)

        empty = await model.embed(())
        assert empty.vectors == ()
        assert empty.usage.requests == 0
        assert base.calls == []

        await model.embed(("alpha",))
        await model.embed(("beta",))
        await model.embed(("alpha",))
        await model.embed(("gamma",))
        await model.embed(("alpha",))
        await model.embed(("beta",))

        assert sum(call.count("alpha") for call in base.calls) == 1
        assert sum(call.count("beta") for call in base.calls) == 2

    asyncio.run(scenario())


def test_lru_eviction_and_recent_access() -> None:
    async def scenario() -> None:
        base = RecordingEmbedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=1)

        await model.embed(("one",))
        await model.embed(("two",))
        await model.embed(("one",))

        assert base.calls == [("one",), ("two",), ("one",)]

    asyncio.run(scenario())


def test_cache_disabled_still_shares_inflight_request() -> None:
    class Blocked(RecordingEmbedding):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            self.started.set()
            await self.release.wait()
            return EmbeddingResult(vectors=tuple((1.0, 1.0) for _ in texts), usage=InferenceUsage(requests=1))

    async def scenario() -> None:
        base = Blocked()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=0)

        first = asyncio.create_task(model.embed(("shared",)))
        second = asyncio.create_task(model.embed(("shared",)))
        await base.started.wait()
        base.release.set()

        assert (await first).vectors == ((1.0, 1.0),)
        assert (await second).vectors == ((1.0, 1.0),)
        assert base.calls == [("shared",)]

        await model.embed(("shared",))
        assert base.calls == [("shared",), ("shared",)]

    asyncio.run(scenario())


def test_failure_is_not_cached_and_retry_succeeds() -> None:
    class Flaky(RecordingEmbedding):
        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            if len(self.calls) == 1:
                raise RuntimeError("temporary")
            return EmbeddingResult(vectors=((1.0, 1.0),), usage=InferenceUsage(requests=1))

    async def scenario() -> None:
        base = Flaky()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)

        with pytest.raises(RuntimeError, match="temporary"):
            await model.embed(("retry",))

        assert (await model.embed(("retry",))).vectors == ((1.0, 1.0),)
        assert len(base.calls) == 2

    asyncio.run(scenario())


def test_vector_count_mismatch_raises_value_error_without_caching() -> None:
    class Invalid(RecordingEmbedding):
        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=1))

    async def scenario() -> None:
        base = Invalid()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)

        with pytest.raises(ValueError):
            await model.embed(("broken",))
        with pytest.raises(ValueError):
            await model.embed(("broken",))

        assert len(base.calls) == 2

    asyncio.run(scenario())


def test_cancelling_one_waiter_does_not_cancel_shared_embedding() -> None:
    class Blocked(RecordingEmbedding):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            self.started.set()
            await self.release.wait()
            return EmbeddingResult(vectors=((1.0, 1.0),), usage=InferenceUsage(requests=1))

    async def scenario() -> None:
        base = Blocked()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)

        cancelled = asyncio.create_task(model.embed(("shared",)))
        survivor = asyncio.create_task(model.embed(("shared",)))
        await base.started.wait()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        base.release.set()
        assert (await survivor).vectors == ((1.0, 1.0),)
        assert base.calls == [("shared",)]

    asyncio.run(scenario())


def test_independent_batches_still_complete_after_one_fails() -> None:
    class Selective(RecordingEmbedding):
        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            if texts == ("fail",):
                raise RuntimeError("failed batch")  # noqa: TRY003
            return EmbeddingResult(vectors=((1.0, 1.0),), usage=InferenceUsage(requests=1))

    async def scenario() -> None:
        base = Selective()
        model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=20, cache_size=2)

        failed = asyncio.create_task(model.embed(("fail",)))
        succeeded = asyncio.create_task(model.embed(("ok",)))

        assert (await succeeded).vectors == ((1.0, 1.0),)
        with pytest.raises(RuntimeError, match="failed batch"):
            await failed

        assert sorted(base.calls) == [("fail",), ("ok",)]
        assert (await model.embed(("ok",))).vectors == ((1.0, 1.0),)
        assert sum(call.count("ok") for call in base.calls) == 1

    asyncio.run(scenario())


def test_cache_hits_do_not_charge_usage_again() -> None:
    async def scenario() -> None:
        base = RecordingEmbedding(InferenceUsage(requests=1, input_tokens=7, output_tokens=0))
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)

        first = await model.embed(("first", "first"))
        assert first.usage.requests == 1
        assert first.usage.input_tokens == 7
        assert first.usage.output_tokens == 0

        cached = await model.embed(("first",))
        assert cached.usage.requests == 0
        assert cached.usage.input_tokens in (None, 0)

    asyncio.run(scenario())


def test_embed_query_uses_separate_lane_for_query_aware_base() -> None:
    class QueryAware(RecordingEmbedding):
        def __init__(self) -> None:
            super().__init__()
            self.query_calls: list[tuple[str, ...]] = []

        async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.query_calls.append(tuple(texts))
            return EmbeddingResult(vectors=tuple((9.0, 9.0) for _ in texts), usage=InferenceUsage(requests=1))

    async def scenario() -> None:
        base = QueryAware()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=4)

        assert isinstance(model, QueryEmbeddingModel)
        assert (await model.embed(("same",))).vectors == ((4.0, 1.0),)
        assert (await model.embed_query(("same",))).vectors == ((9.0, 9.0),)
        assert base.calls == [("same",)]
        assert base.query_calls == [("same",)]

        await model.embed_query(("same",))
        await model.embed(("same",))
        assert base.query_calls == [("same",)]
        assert base.calls == [("same",)]

    asyncio.run(scenario())


def test_embed_query_falls_back_to_document_lane_for_symmetric_base() -> None:
    async def scenario() -> None:
        base = RecordingEmbedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=2)

        assert (await model.embed(("same",))).vectors == ((4.0, 1.0),)
        assert (await model.embed_query(("same",))).vectors == ((4.0, 1.0),)
        assert base.calls == [("same",)]

    asyncio.run(scenario())


def test_base_exception_reaches_waiters_without_hanging() -> None:
    class Fatal(BaseException):
        pass

    class FatalEmbedding(RecordingEmbedding):
        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            raise Fatal("fatal")

    async def scenario() -> None:
        model = CoalescingEmbeddingModel(FatalEmbedding(), coalesce_ms=0)
        raised = False
        try:
            await asyncio.wait_for(model.embed(("fatal",)), timeout=1)
        except Fatal:
            raised = True
        assert raised

    asyncio.run(scenario())


def test_profile_is_exposed() -> None:
    base = RecordingEmbedding()
    assert CoalescingEmbeddingModel(base).profile == base.profile


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_batch_size": 0},
        {"max_batch_size": -1},
        {"max_batch_size": True},
        {"max_batch_size": 1.5},
        {"max_batch_size": "6"},
        {"coalesce_ms": -0.1},
        {"coalesce_ms": True},
        {"coalesce_ms": "5"},
        {"cache_size": -1},
        {"cache_size": True},
        {"cache_size": 1.5},
    ],
)
def test_constructor_rejects_invalid_bounds(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CoalescingEmbeddingModel(RecordingEmbedding(), **kwargs)


def test_wrapper_drives_native_memory_vector_search(tmp_path) -> None:
    class SemanticEmbedding:
        profile = EmbeddingProfile(profile_id="coalescing-native", model="offline", dimension=2)

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            return EmbeddingResult(
                vectors=tuple((1.0, 0.0) if "alpha" in text.lower() else (0.0, 1.0) for text in texts),
                usage=InferenceUsage(requests=1),
            )

    async def scenario() -> None:
        model = CoalescingEmbeddingModel(SemanticEmbedding(), coalesce_ms=0)
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'coalescing.db'}"))

        async with open_builtin_contexts(config, embedding_model=model) as contexts:
            service = (await contexts.get("coalescing-native")).artifacts.memory
            memory = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha launch decision."),
                    MemoryEntryInput(kind="fact", text="Beta operational note."),
                ),
                mode="append",
            )
            assert memory is not None

            result = await service.search("alpha", memories=(memory,), limit=1, mode="vector")
            assert result.hits[0].text == "Alpha launch decision."

    asyncio.run(scenario())


def test_native_request_budget_merges_six_scopes(tmp_path) -> None:
    class MeteredEmbedding:
        profile = EmbeddingProfile(profile_id="request-budget-v1", model="offline", dimension=24)

        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            vectors = tuple(tuple(float(f"needle{index} fixture" == value) for index in range(24)) for value in texts)
            return EmbeddingResult(
                vectors=vectors,
                usage=InferenceUsage(requests=1, input_tokens=len(texts) * 7, output_tokens=0),
            )

    async def scenario() -> None:
        base = MeteredEmbedding()
        model = CoalescingEmbeddingModel(base, max_batch_size=6, coalesce_ms=0, cache_size=48)
        parties = 6

        class StartTogether:
            profile = model.profile

            def __init__(self) -> None:
                self.arrived = 0
                self.release = asyncio.Event()

            async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
                if self.arrived < parties:
                    self.arrived += 1
                    if self.arrived == parties:
                        self.release.set()
                    await self.release.wait()
                return await model.embed(texts)

        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'batching.db'}"))
        async with open_builtin_contexts(config, embedding_model=StartTogether()) as contexts:
            services = [(await contexts.get(f"scope-{index}")).artifacts.memory for index in range(parties)]
            texts = tuple(f"needle{index} fixture" for index in range(24))
            entries = tuple(MemoryEntryInput(kind="fact", text=value) for value in texts)
            memories = await asyncio.wait_for(
                asyncio.gather(
                    *(service.remember(memory=None, entries=entries, mode="append") for service in services)
                ),
                timeout=30,
            )

            assert len(base.calls) == math.ceil(len(texts) / 6)
            assert all(1 <= len(call) <= 6 for call in base.calls)
            assert sorted(value for call in base.calls for value in call) == sorted(texts)

            for index, (service, memory) in enumerate(zip(services, memories, strict=True)):
                assert memory is not None
                result = await service.search(texts[index], memories=(memory,), limit=1, mode="vector")
                assert result.hits[0].text == texts[index]

            assert len(base.calls) == 4, "cache hits must not issue provider requests"

    asyncio.run(scenario())

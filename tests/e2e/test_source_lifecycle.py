"""Source lifecycle through the native, persisted runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput, MemoryService
from powercontext.builtin.inference import EmbeddingModel, EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    GetMemoryEntryRequest,
    GetTopicMemoryRequest,
    PrepareContextRequest,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    RuntimeConfig,
    SearchMemoryRequest,
    SearchTopicMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft, ScopeNotFoundError
from powercontext.sources import SourceRef


@asynccontextmanager
async def _runtime(directory: Path, embedding: EmbeddingModel | None = None) -> AsyncIterator[BuiltinRuntime]:
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'lifecycle.db'}"),
        runtime=RuntimeConfig(artifact_processing_families=()),
    )
    async with open_builtin_runtime(
        config, scheduler_path=directory / "scheduler.db", embedding_model=embedding
    ) as runtime:
        yield runtime


async def _scope(runtime: BuiltinRuntime, key: str = "lifecycle") -> str:
    assert runtime.scopes is not None
    scope = await runtime.scopes.create(ScopeDraft(title=key, summary="Lifecycle evidence", idempotency_key=key))
    return scope.scope_id


async def _source(runtime: BuiltinRuntime, scope: str, name: str = "source-a") -> SourceRef:
    receipt = await runtime.sources.for_scope(scope).capture(
        CaptureSource(source_id=name, content=f"Evidence {name}", metadata={})
    )
    return receipt.source_ref


async def _memory(runtime: BuiltinRuntime, scope: str, text: str = "Lifecycle memory evidence") -> MemoryCitation:
    result = await runtime.memory.for_scope(scope).remember(
        RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=text),))
    )
    assert result.entry is not None
    return result.entry.citation


async def _topic(runtime: BuiltinRuntime, scope: str, label: str = "Lifecycle") -> ArtifactRef:
    result = await runtime.records.for_scope(scope).create_artifact(
        "topic-memory",
        ArtifactWrite(
            content={
                "title": f"{label} topic",
                "summary": f"{label} summary evidence",
                "detail": f"# {label} topic\n\nComplete original topic evidence.",
            }
        ),
    )
    return ArtifactRef(family=result.family, artifact_id=result.artifact_id, revision=result.revision)


def test_registration_preserves_original_references_without_memory_revisions(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source = await _source(runtime, scope)
            citation = await _memory(runtime, scope)
            topic = await _topic(runtime, scope)
            memory = runtime.memory.for_scope(scope)
            before = await memory.list(include_inactive=True)
            lifecycle = runtime.lifecycle.for_scope(scope)

            await lifecycle.register_source("source-a", source, revision=3)
            await lifecycle.register_memory("memory-a", citation, parents=("source-a",), policy="all")
            await lifecycle.register_topic("topic-a", topic, parents=("memory-a",), policy="any")
            await lifecycle.register_source("source-a", source, revision=3)
            await lifecycle.register_memory("memory-a", citation, parents=("source-a",), policy="all")
            await lifecycle.register_topic("topic-a", topic, parents=("memory-a",), policy="any")

            for node_id, reference in (("source-a", source), ("memory-a", citation), ("topic-a", topic)):
                for purpose in ("generation", "audit"):
                    resolved = await lifecycle.resolve(node_id, purpose=purpose)
                    assert type(resolved) is type(reference)
                    assert resolved == reference
            assert await memory.list(include_inactive=True) == before
            assert (
                await memory.get(GetMemoryEntryRequest(citation=citation))
            ).entry.text == "Lifecycle memory evidence"
            original = await runtime.topic_memory.for_scope(scope).get(GetTopicMemoryRequest(artifact=topic))
            assert original.topic.content.detail == "# Lifecycle topic\n\nComplete original topic evidence."

    asyncio.run(scenario())


def test_registration_conflicts_leave_original_bindings_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            first_source = await _source(runtime, scope)
            second_source = await _source(runtime, scope, "source-b")
            first_memory = await _memory(runtime, scope)
            second_memory = await _memory(runtime, scope, "A different independent fact")
            topic = await _topic(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", first_source, revision=3)
            await lifecycle.register_source("source-b", second_source, revision=3)
            await lifecycle.register_memory("memory-a", first_memory, parents=("source-a",), policy="all")
            await lifecycle.register_topic("topic-a", topic, parents=("memory-a",), policy="any")

            conflicts = (
                lambda: lifecycle.register_source("source-a", second_source, revision=3),
                lambda: lifecycle.register_source("source-a", first_source, revision=4),
                lambda: lifecycle.register_memory("source-a", first_memory, parents=("source-b",), policy="all"),
                lambda: lifecycle.register_memory("memory-a", second_memory, parents=("source-a",), policy="all"),
                lambda: lifecycle.register_memory("memory-a", first_memory, parents=("source-b",), policy="all"),
                lambda: lifecycle.register_memory("memory-a", first_memory, parents=("source-a",), policy="any"),
                lambda: lifecycle.register_topic("topic-a", topic, parents=("source-b",), policy="any"),
                lambda: lifecycle.register_topic("topic-a", topic, parents=("memory-a",), policy="all"),
            )
            for conflict in conflicts:
                with pytest.raises(SourceLifecycleError):
                    await conflict()

            assert await lifecycle.resolve("source-a", purpose="audit") == first_source
            assert await lifecycle.resolve("memory-a", purpose="audit") == first_memory
            assert await lifecycle.resolve("topic-a", purpose="audit") == topic

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("parents", "policy"),
    [
        ((), "all"),
        (("source-a", "source-a"), "all"),
        (("unknown-parent",), "all"),
        (("memory-a",), "all"),
        (("source-a", "memory-a"), "any"),
        (("source-a",), "invalid-policy"),
    ],
)
def test_invalid_dependencies_do_not_register_a_node(tmp_path: Path, parents: tuple[str, ...], policy: str) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source = await _source(runtime, scope)
            citation = await _memory(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", source, revision=3)

            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("memory-a", citation, parents=parents, policy=policy)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-a", purpose="audit")

            await lifecycle.register_memory("memory-a", citation, parents=("source-a",), policy="all")
            assert await lifecycle.resolve("memory-a", purpose="generation") == citation

    asyncio.run(scenario())


def test_dependency_order_is_not_a_different_binding(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            first = await _source(runtime, scope)
            second = await _source(runtime, scope, "source-b")
            citation = await _memory(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", first, revision=3)
            await lifecycle.register_source("source-b", second, revision=3)
            await lifecycle.register_memory("memory-a", citation, parents=("source-a", "source-b"), policy="all")
            before = await runtime.memory.for_scope(scope).list(include_inactive=True)

            await lifecycle.register_memory("memory-a", citation, parents=("source-b", "source-a"), policy="all")

            assert await lifecycle.resolve("memory-a", purpose="generation") == citation
            assert await runtime.memory.for_scope(scope).list(include_inactive=True) == before

    asyncio.run(scenario())


def test_registration_rejects_missing_cross_scope_and_wrong_family_references(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            other_scope = await _scope(runtime, "other")
            local_source = await _source(runtime, scope)
            foreign_source = await _source(runtime, other_scope, "foreign-source")
            local_memory = await _memory(runtime, scope)
            foreign_memory = await _memory(runtime, other_scope)
            foreign_topic = await _topic(runtime, other_scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", local_source, revision=3)

            invalid_sources = (foreign_source, SourceRef(source_type="content", source_id="never-captured"))
            for reference in invalid_sources:
                with pytest.raises(SourceLifecycleError):
                    await lifecycle.register_source("invalid-source", reference, revision=3)
            invalid_memories = (
                foreign_memory,
                local_memory.model_copy(update={"entry_version_id": "missing-version"}),
                local_memory.model_copy(
                    update={"memory_ref": local_memory.memory_ref.model_copy(update={"revision": 999})}
                ),
            )
            for citation in invalid_memories:
                with pytest.raises(SourceLifecycleError):
                    await lifecycle.register_memory("invalid-memory", citation, parents=("source-a",), policy="all")
            invalid_topics = (
                foreign_topic,
                ArtifactRef(family="topic-memory", artifact_id="missing-topic", revision=1),
                local_memory.memory_ref,
            )
            for reference in invalid_topics:
                with pytest.raises(SourceLifecycleError):
                    await lifecycle.register_topic("invalid-topic", reference, parents=("source-a",), policy="all")
            for node_id in ("invalid-source", "invalid-memory", "invalid-topic"):
                with pytest.raises(SourceLifecycleError):
                    await lifecycle.resolve(node_id, purpose="audit")

    asyncio.run(scenario())


def test_resolve_rejects_unknown_node_and_purpose(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source = await _source(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", source, revision=3)

            for purpose in ("generation", "audit"):
                with pytest.raises(SourceLifecycleError):
                    await lifecycle.resolve("unknown-node", purpose=purpose)
            for purpose in ("", "archive", "Generation"):
                with pytest.raises(SourceLifecycleError):
                    await lifecycle.resolve("source-a", purpose=purpose)
            assert await lifecycle.resolve("source-a", purpose="generation") == source

    asyncio.run(scenario())


def test_registration_survives_restart_with_exact_old_memory_citation(tmp_path: Path) -> None:
    async def scenario() -> None:
        node_id = "  来源" + "证据" * 300 + "  "
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source = await _source(runtime, scope)
            citation = await _memory(runtime, scope)
            await _memory(runtime, scope, "A later write advances the Memory artifact revision")
            topic = await _topic(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source(node_id, source, revision=3)
            await lifecycle.register_memory("memory-a", citation, parents=(node_id,), policy="all")
            await lifecycle.register_topic("topic-a", topic, parents=("memory-a",), policy="any")

        async with _runtime(tmp_path) as reopened:
            lifecycle = reopened.lifecycle.for_scope(scope)
            assert await lifecycle.resolve(node_id, purpose="generation") == source
            assert await lifecycle.resolve("memory-a", purpose="generation") == citation
            assert await lifecycle.resolve("topic-a", purpose="audit") == topic
            record = await reopened.memory.for_scope(scope).get(GetMemoryEntryRequest(citation=citation))
            assert record.entry.text == "Lifecycle memory evidence"
            assert record.entry.entry_version_id == citation.entry_version_id

    asyncio.run(scenario())


def test_lifecycle_requires_a_registered_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source = await _source(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope("project:unregistered")

            with pytest.raises(ScopeNotFoundError):
                await lifecycle.register_source("source-a", source, revision=3)
            with pytest.raises(ScopeNotFoundError):
                await lifecycle.resolve("source-a", purpose="audit")

    asyncio.run(scenario())


def _event(
    node_id: str = "source-a",
    *,
    sequence: int = 1,
    available: bool = False,
    revision: int = 3,
    reason: str = "Caller declared source availability",
) -> SourceLifecycleEvent:
    return SourceLifecycleEvent(
        node_id=node_id, sequence=sequence, available=available, revision=revision, reason=reason
    )


async def _register_graph(runtime: BuiltinRuntime, scope: str) -> dict[str, SourceRef | MemoryCitation | ArtifactRef]:
    lifecycle = runtime.lifecycle.for_scope(scope)
    references: dict[str, SourceRef | MemoryCitation | ArtifactRef] = {}
    for name in ("source-a", "source-b", "source-c", "source-d"):
        source = await _source(runtime, scope, name)
        references[name] = source
        await lifecycle.register_source(name, source, revision=3)
    memory_bindings = (
        ("memory-01", ("source-a",), "all"),
        ("memory-02", ("source-a", "source-b"), "any"),
        ("memory-03", ("source-a", "source-b"), "all"),
        ("memory-04", ("source-c",), "all"),
        ("memory-05", ("memory-01",), "all"),
        ("memory-06", ("memory-02", "memory-04"), "all"),
        ("memory-07", ("memory-01", "memory-04"), "any"),
        ("memory-08", ("source-b",), "all"),
        ("memory-09", ("memory-03", "memory-08"), "all"),
        ("memory-10", ("source-d",), "all"),
        ("memory-11", ("memory-05", "memory-06"), "all"),
        ("memory-12", ("memory-09", "memory-10"), "any"),
    )
    for name, parents, policy in memory_bindings:
        citation = await _memory(runtime, scope, f"Memory evidence {name}")
        references[name] = citation
        await lifecycle.register_memory(name, citation, parents=parents, policy=policy)
    topic_bindings = (
        ("topic-01", ("memory-05", "memory-11"), "all"),
        ("topic-02", ("memory-06", "memory-07"), "any"),
        ("topic-03", ("memory-09",), "all"),
        ("topic-04", ("memory-10", "memory-12"), "all"),
    )
    for name, parents, policy in topic_bindings:
        reference = await _topic(runtime, scope)
        references[name] = reference
        await lifecycle.register_topic(name, reference, parents=parents, policy=policy)
    return references


def test_multilayer_all_any_propagates_and_preserves_original_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            references = await _register_graph(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            memory = runtime.memory.for_scope(scope)
            before = await memory.list(include_inactive=True)
            expected_active = [
                "memory-02",
                "memory-04",
                "memory-06",
                "memory-07",
                "memory-08",
                "memory-10",
                "memory-12",
                "source-b",
                "source-c",
                "source-d",
                "topic-02",
                "topic-04",
            ]

            assert await lifecycle.apply(_event()) == expected_active
            current = await memory.list(include_inactive=True)
            assert {item.entry.entry_id: item.state for item in current.entries} == {
                reference.entry_id: "active" if name in expected_active else "inactive"
                for name, reference in references.items()
                if isinstance(reference, MemoryCitation)
            }
            assert {item.entry.entry_version_id for item in current.entries} == {
                item.entry.entry_version_id for item in before.entries
            }
            for name, reference in references.items():
                assert await lifecycle.resolve(name, purpose="audit") == reference
                if name in expected_active:
                    assert await lifecycle.resolve(name, purpose="generation") == reference
                else:
                    with pytest.raises(SourceLifecycleError):
                        await lifecycle.resolve(name, purpose="generation")
                if isinstance(reference, MemoryCitation):
                    original = await memory.get(GetMemoryEntryRequest(citation=reference))
                    assert original.entry.text == f"Memory evidence {name}"
                    assert original.entry.entry_version_id == reference.entry_version_id
                elif isinstance(reference, ArtifactRef):
                    original_topic = await runtime.topic_memory.for_scope(scope).get(
                        GetTopicMemoryRequest(artifact=reference)
                    )
                    assert (
                        original_topic.topic.content.detail == "# Lifecycle topic\n\nComplete original topic evidence."
                    )

            assert await lifecycle.apply(_event("source-b")) == [
                "memory-04",
                "memory-07",
                "memory-10",
                "memory-12",
                "source-c",
                "source-d",
                "topic-02",
                "topic-04",
            ]
            assert await lifecycle.apply(_event("source-a", sequence=2, available=True)) == [
                "memory-01",
                "memory-02",
                "memory-04",
                "memory-05",
                "memory-06",
                "memory-07",
                "memory-10",
                "memory-11",
                "memory-12",
                "source-a",
                "source-c",
                "source-d",
                "topic-01",
                "topic-02",
                "topic-04",
            ]
            assert await lifecycle.apply(_event("source-b", sequence=2, available=True)) == sorted(references)
            restored = await memory.list(include_inactive=True)
            assert all(item.state == "active" for item in restored.entries)
            assert {item.entry.entry_version_id for item in restored.entries} == {
                item.entry.entry_version_id for item in before.entries
            }
            for name, reference in references.items():
                assert await lifecycle.resolve(name, purpose="generation") == reference

    asyncio.run(scenario())


def test_event_validation_replay_and_per_source_sequences(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source_a = await _source(runtime, scope)
            source_b = await _source(runtime, scope, "source-b")
            citation = await _memory(runtime, scope)
            topic = await _topic(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", source_a, revision=3)
            await lifecycle.register_source("source-b", source_b, revision=7)
            await lifecycle.register_memory("memory-a", citation, parents=("source-a",), policy="all")
            await lifecycle.register_topic("topic-a", topic, parents=("memory-a",), policy="any")
            memory = runtime.memory.for_scope(scope)
            before = await memory.list(include_inactive=True)
            for event in (_event("unknown"), _event("memory-a"), _event("topic-a"), _event(revision=4)):
                with pytest.raises(SourceLifecycleError):
                    await lifecycle.apply(event)
                assert await memory.list(include_inactive=True) == before

            accepted = _event(sequence=3)
            assert await lifecycle.apply(accepted) == ["source-b"]
            after = await memory.list(include_inactive=True)
            assert await lifecycle.apply(accepted) == ["source-b"]
            assert await memory.list(include_inactive=True) == after
            for event in (
                _event(sequence=2),
                _event(sequence=3, available=True),
                _event(sequence=3, reason="Different reason at the same sequence"),
                _event(sequence=3, revision=4),
            ):
                with pytest.raises(SourceLifecycleError):
                    await lifecycle.apply(event)
                assert await memory.list(include_inactive=True) == after

            assert await lifecycle.apply(_event("source-b", revision=7)) == []
            assert await lifecycle.apply(_event(sequence=4, available=True)) == ["memory-a", "source-a", "topic-a"]
            restored = await memory.list(include_inactive=True)
            # An event may advance its Source sequence without changing eligibility.
            assert await lifecycle.apply(_event(sequence=5, available=True)) == ["memory-a", "source-a", "topic-a"]
            assert await memory.list(include_inactive=True) == restored
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event(sequence=4, available=True))

    asyncio.run(scenario())


def test_registration_after_withdrawal_immediately_uses_parent_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source_a = await _source(runtime, scope)
            source_b = await _source(runtime, scope, "source-b")
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", source_a, revision=3)
            await lifecycle.register_source("source-b", source_b, revision=3)
            assert await lifecycle.apply(_event()) == ["source-b"]
            await lifecycle.register_source("source-a", source_a, revision=3)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("source-a", purpose="generation")

            all_memory = await _memory(runtime, scope, "All parents support this whole fact")
            any_memory = await _memory(runtime, scope, "Any parent supports this whole fact")
            all_topic = await _topic(runtime, scope)
            any_topic = await _topic(runtime, scope)
            await lifecycle.register_memory("memory-all", all_memory, parents=("source-a", "source-b"), policy="all")
            await lifecycle.register_memory("memory-any", any_memory, parents=("source-a", "source-b"), policy="any")
            await lifecycle.register_topic("topic-all", all_topic, parents=("memory-all",), policy="all")
            await lifecycle.register_topic("topic-any", any_topic, parents=("memory-all", "memory-any"), policy="any")

            current = await runtime.memory.for_scope(scope).list(include_inactive=True)
            assert {item.entry.entry_id: item.state for item in current.entries} == {
                all_memory.entry_id: "inactive",
                any_memory.entry_id: "active",
            }
            for node_id in ("memory-all", "topic-all"):
                with pytest.raises(SourceLifecycleError):
                    await lifecycle.resolve(node_id, purpose="generation")
            assert await lifecycle.resolve("memory-any", purpose="generation") == any_memory
            assert await lifecycle.resolve("topic-any", purpose="generation") == any_topic
            assert await lifecycle.resolve("memory-all", purpose="audit") == all_memory
            assert await lifecycle.resolve("topic-all", purpose="audit") == all_topic
            before_replay = current.memory_ref
            assert await lifecycle.apply(_event(sequence=2)) == ["memory-any", "source-b", "topic-any"]
            assert (await runtime.memory.for_scope(scope).list()).memory_ref == before_replay

    asyncio.run(scenario())


def test_registration_restores_an_inactive_entry_with_original_body_version(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source = await _source(runtime, scope)
            citation = await _memory(runtime, scope)
            memory = runtime.memory.for_scope(scope)
            await memory.retire(RetireMemoryEntryRequest(citation=citation, reason="Manually inactive before binding"))
            assert (await memory.list(include_inactive=True)).entries[0].state == "inactive"
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", source, revision=3)

            await lifecycle.register_memory("memory-a", citation, parents=("source-a",), policy="all")

            current = await memory.list()
            assert len(current.entries) == 1
            assert current.entries[0].entry.entry_version_id == citation.entry_version_id
            assert await lifecycle.resolve("memory-a", purpose="generation") == citation

    asyncio.run(scenario())


def test_shared_memory_remains_native_active_while_any_binding_has_support(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source_a = await _source(runtime, scope)
            source_b = await _source(runtime, scope, "source-b")
            citation = await _memory(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", source_a, revision=3)
            await lifecycle.register_source("source-b", source_b, revision=3)
            await lifecycle.register_memory("binding-a", citation, parents=("source-a",), policy="all")
            await lifecycle.register_memory("binding-b", citation, parents=("source-b",), policy="all")
            memory = runtime.memory.for_scope(scope)
            before = await memory.list()

            assert await lifecycle.apply(_event()) == ["binding-b", "source-b"]
            assert await memory.list() == before
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("binding-a", purpose="generation")
            assert await lifecycle.resolve("binding-b", purpose="generation") == citation
            assert await lifecycle.apply(_event("source-b")) == []
            assert (await memory.list()).entries == ()
            assert await lifecycle.apply(_event(sequence=2, available=True)) == ["binding-a", "source-a"]
            assert (await memory.list()).entries[0].entry.entry_version_id == citation.entry_version_id

    asyncio.run(scenario())


def test_propagation_follows_dependencies_instead_of_node_id_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source = await _source(runtime, scope)
            parent = await _memory(runtime, scope, "Parent memory")
            leaf = await _memory(runtime, scope, "Leaf memory")
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("zz-source", source, revision=3)
            await lifecycle.register_memory("zz-memory", parent, parents=("zz-source",), policy="all")
            await lifecycle.register_memory("aa-leaf", leaf, parents=("zz-memory",), policy="all")

            assert await lifecycle.apply(_event("zz-source")) == []
            assert (await runtime.memory.for_scope(scope).list()).entries == ()
            assert await lifecycle.apply(_event("zz-source", sequence=2, available=True)) == [
                "aa-leaf",
                "zz-memory",
                "zz-source",
            ]

    asyncio.run(scenario())


class _InterruptedLifecycleWrite(RuntimeError):
    pass


def test_native_write_failure_rolls_back_graph_and_retries_the_same_event(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            references = await _register_graph(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            memory = runtime.memory.for_scope(scope)
            before = await memory.list(include_inactive=True)
            before_changes = await memory.changes()
            original_forget = MemoryService.forget

            async def interrupted_forget(service, current, *, entries, reason=None):
                await original_forget(service, current, entries=entries, reason=reason)
                raise _InterruptedLifecycleWrite

            with monkeypatch.context() as patch:
                patch.setattr(MemoryService, "forget", interrupted_forget)
                with pytest.raises(_InterruptedLifecycleWrite):
                    await lifecycle.apply(_event())

            assert await memory.list(include_inactive=True) == before
            assert await memory.changes() == before_changes
            for name, reference in references.items():
                assert await lifecycle.resolve(name, purpose="generation") == reference

            active = await lifecycle.apply(_event())
            assert "memory-01" not in active
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-01", purpose="generation")
            completed = await memory.list(include_inactive=True)
            changes = await memory.changes()
            assert await lifecycle.apply(_event()) == active
            assert await memory.list(include_inactive=True) == completed
            assert await memory.changes() == changes

    asyncio.run(scenario())


def test_cancelled_propagation_rolls_back_native_writes_before_retry(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source = await _source(runtime, scope)
            citation = await _memory(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", source, revision=3)
            await lifecycle.register_memory("memory-a", citation, parents=("source-a",), policy="all")
            memory = runtime.memory.for_scope(scope)
            before = await memory.list(include_inactive=True)
            original_forget = MemoryService.forget
            written = asyncio.Event()
            resume = asyncio.Event()

            async def paused_forget(service, current, *, entries, reason=None):
                updated = await original_forget(service, current, entries=entries, reason=reason)
                written.set()
                await resume.wait()
                return updated

            with monkeypatch.context() as patch:
                patch.setattr(MemoryService, "forget", paused_forget)
                async with asyncio.timeout(10):
                    task = asyncio.create_task(lifecycle.apply(_event()))
                    try:
                        await written.wait()
                    finally:
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task

            assert await memory.list(include_inactive=True) == before
            assert await lifecycle.resolve("source-a", purpose="generation") == source
            assert await lifecycle.resolve("memory-a", purpose="generation") == citation
            assert await lifecycle.apply(_event()) == []
            assert (await memory.list()).entries == ()

    asyncio.run(scenario())


def test_event_state_and_exact_replay_survive_restart_without_cross_scope_effects(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            other_scope = await _scope(runtime, "independent")
            source = await _source(runtime, scope)
            other_source = await _source(runtime, other_scope)
            citation = await _memory(runtime, scope)
            other_citation = await _memory(runtime, other_scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            other_lifecycle = runtime.lifecycle.for_scope(other_scope)
            await lifecycle.register_source("source-a", source, revision=3)
            await lifecycle.register_memory("memory-a", citation, parents=("source-a",), policy="all")
            await other_lifecycle.register_source("source-a", other_source, revision=7)
            await other_lifecycle.register_memory("memory-a", other_citation, parents=("source-a",), policy="all")
            other_before = await runtime.memory.for_scope(other_scope).list(include_inactive=True)
            assert await lifecycle.apply(_event(sequence=4)) == []
            withdrawn = await runtime.memory.for_scope(scope).list(include_inactive=True)

        async with _runtime(tmp_path) as reopened:
            lifecycle = reopened.lifecycle.for_scope(scope)
            other_lifecycle = reopened.lifecycle.for_scope(other_scope)
            assert await lifecycle.resolve("source-a", purpose="audit") == source
            assert await lifecycle.resolve("memory-a", purpose="audit") == citation
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-a", purpose="generation")
            assert await lifecycle.apply(_event(sequence=4)) == []
            assert await reopened.memory.for_scope(scope).list(include_inactive=True) == withdrawn
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event(sequence=3))
            await lifecycle.register_source("source-a", source, revision=3)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("source-a", purpose="generation")
            assert await other_lifecycle.resolve("source-a", purpose="generation") == other_source
            assert await other_lifecycle.resolve("memory-a", purpose="generation") == other_citation
            assert await reopened.memory.for_scope(other_scope).list(include_inactive=True) == other_before
            assert await lifecycle.apply(_event(sequence=5, available=True)) == ["memory-a", "source-a"]
            assert await lifecycle.resolve("memory-a", purpose="generation") == citation

    asyncio.run(scenario())


class _LifecycleEmbedding:
    profile = EmbeddingProfile(profile_id="lifecycle-test", model="offline", dimension=2)

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple((1.0, 0.0) if "scarlet" in text.casefold() else (0.0, 1.0) for text in texts)
        )


@pytest.mark.parametrize("vector", [False, True])
def test_native_search_context_and_topic_history_follow_lifecycle_across_restart(tmp_path: Path, vector: bool) -> None:
    async def scenario() -> None:
        embedding = _LifecycleEmbedding() if vector else None
        async with _runtime(tmp_path, embedding) as runtime:
            scope = await _scope(runtime)
            source_a = await _source(runtime, scope)
            source_b = await _source(runtime, scope, "source-b")
            revoked_memory = await _memory(runtime, scope, "Scarlet withdrawn memory evidence")
            kept_memory = await _memory(runtime, scope, "Cobalt independent memory evidence")
            await _memory(runtime, scope, "Emerald unregistered memory evidence")
            revoked_topic = await _topic(runtime, scope, "Scarlet")
            kept_topic = await _topic(runtime, scope, "Cobalt")
            unregistered_topic = await _topic(runtime, scope, "Emerald")
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", source_a, revision=3)
            await lifecycle.register_source("source-b", source_b, revision=3)
            await lifecycle.register_memory("memory-a", revoked_memory, parents=("source-a",), policy="all")
            await lifecycle.register_memory("memory-b", kept_memory, parents=("source-b",), policy="all")
            await lifecycle.register_topic("topic-a", revoked_topic, parents=("memory-a",), policy="all")
            await lifecycle.register_topic("topic-b", kept_topic, parents=("memory-b",), policy="any")
            topic_service = runtime.topic_memory.for_scope(scope)
            original_topic = await topic_service.get(GetTopicMemoryRequest(artifact=revoked_topic))
            before = await topic_service.search(SearchTopicMemoryRequest(query="Scarlet", limit=1))
            assert [hit.artifact_ref for hit in before.hits] == [revoked_topic]
            assert await lifecycle.apply(_event()) == ["memory-b", "source-b", "topic-b"]

            modes = ("fts", "vector", "hybrid") if vector else ("fts",)
            for mode in modes:
                page = await runtime.memory.for_scope(scope).search(SearchMemoryRequest(query="Scarlet", mode=mode))
                assert revoked_memory.entry_id not in {hit.entry_id for hit in page.hits}
            topics = await topic_service.search(SearchTopicMemoryRequest(query="Scarlet"))
            assert revoked_topic not in [hit.artifact_ref for hit in topics.hits]
            assert {item.artifact_ref.artifact_id for item in await topic_service.browse(limit=20)} == {
                kept_topic.artifact_id,
                unregistered_topic.artifact_id,
            }
            context = await runtime.context.for_scope(scope).prepare(PrepareContextRequest(query="Scarlet"))
            assert "Scarlet withdrawn memory evidence" not in (context.content or "")
            assert "Scarlet topic" not in (context.content or "")
            assert revoked_topic.artifact_id not in (context.content or "")
            assert await topic_service.get(GetTopicMemoryRequest(artifact=revoked_topic)) == original_topic
            kept = await topic_service.search(SearchTopicMemoryRequest(query="Cobalt"))
            assert kept_topic in [hit.artifact_ref for hit in kept.hits]

        async with _runtime(tmp_path, embedding) as reopened:
            topic_service = reopened.topic_memory.for_scope(scope)
            topics = await topic_service.search(SearchTopicMemoryRequest(query="Scarlet"))
            assert revoked_topic not in [hit.artifact_ref for hit in topics.hits]
            assert await topic_service.get(GetTopicMemoryRequest(artifact=revoked_topic)) == original_topic
            lifecycle = reopened.lifecycle.for_scope(scope)
            assert await lifecycle.resolve("topic-a", purpose="audit") == revoked_topic
            assert await lifecycle.apply(_event(sequence=2, available=True)) == [
                "memory-a",
                "memory-b",
                "source-a",
                "source-b",
                "topic-a",
                "topic-b",
            ]
            restored = await topic_service.search(SearchTopicMemoryRequest(query="Scarlet", limit=1))
            assert [hit.artifact_ref for hit in restored.hits] == [revoked_topic]
            assert await topic_service.get(GetTopicMemoryRequest(artifact=revoked_topic)) == original_topic
            context = await reopened.context.for_scope(scope).prepare(PrepareContextRequest(query="Scarlet"))
            assert "Scarlet withdrawn memory evidence" in (context.content or "")
            assert "Scarlet topic" in (context.content or "")
            for node_id, reference in (("memory-a", revoked_memory), ("topic-a", revoked_topic)):
                assert await lifecycle.resolve(node_id, purpose="generation") == reference

    asyncio.run(scenario())


def test_twenty_node_graph_filters_shared_topics_in_native_search_and_context(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            references = await _register_graph(runtime, scope)
            topic_refs = {name: ref for name, ref in references.items() if isinstance(ref, ArtifactRef)}
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.apply(_event())
            topics = await runtime.topic_memory.for_scope(scope).search(
                SearchTopicMemoryRequest(query="Lifecycle", limit=20)
            )
            assert {hit.artifact_ref.artifact_id for hit in topics.hits} == {
                topic_refs["topic-02"].artifact_id,
                topic_refs["topic-04"].artifact_id,
            }
            context = await runtime.context.for_scope(scope).prepare(PrepareContextRequest(query="Lifecycle"))
            for node_id in ("topic-01", "topic-03"):
                assert topic_refs[node_id].artifact_id not in (context.content or "")
            for node_id in ("topic-02", "topic-04"):
                assert topic_refs[node_id].artifact_id in (context.content or "")

    asyncio.run(scenario())


@pytest.mark.parametrize("revision", [0, -1, True, 3.0, "3", None])
def test_source_registration_rejects_invalid_lifecycle_revisions(tmp_path: Path, revision) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source = await _source(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_source("source-a", source, revision=revision)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("source-a", purpose="audit")

    asyncio.run(scenario())


def test_shared_topic_remains_searchable_while_another_binding_has_support(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path) as runtime:
            scope = await _scope(runtime)
            source_a = await _source(runtime, scope)
            source_b = await _source(runtime, scope, "source-b")
            topic_ref = await _topic(runtime, scope)
            lifecycle = runtime.lifecycle.for_scope(scope)
            await lifecycle.register_source("source-a", source_a, revision=3)
            await lifecycle.register_source("source-b", source_b, revision=3)
            await lifecycle.register_topic("topic-a", topic_ref, parents=("source-a",), policy="all")
            await lifecycle.register_topic("topic-b", topic_ref, parents=("source-b",), policy="all")
            topics = runtime.topic_memory.for_scope(scope)

            assert await lifecycle.apply(_event()) == ["source-b", "topic-b"]
            result = await topics.search(SearchTopicMemoryRequest(query="Lifecycle"))
            assert [hit.artifact_ref for hit in result.hits] == [topic_ref]
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("topic-a", purpose="generation")
            assert await lifecycle.resolve("topic-b", purpose="generation") == topic_ref
            assert await lifecycle.apply(_event("source-b")) == []
            assert (await topics.search(SearchTopicMemoryRequest(query="Lifecycle"))).hits == ()
            assert await lifecycle.apply(_event(sequence=2, available=True)) == ["source-a", "topic-a"]
            result = await topics.search(SearchTopicMemoryRequest(query="Lifecycle"))
            assert [hit.artifact_ref for hit in result.hits] == [topic_ref]

    asyncio.run(scenario())

"""Persisted source dependency registration and generation eligibility."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCitationError, MemoryEntryNotFoundError
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.source_lifecycle import SourceLifecycleRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

__all__ = ["SourceLifecycleError", "SourceLifecycleEvent"]

LifecycleReference: TypeAlias = SourceRef | MemoryCitation | ArtifactRef


class SourceLifecycleError(ValueError):
    """An invalid lifecycle registration, event, or resolution request."""

    def __init__(self, issue: str) -> None:
        super().__init__(f"Invalid source lifecycle operation: {issue}")


def _required_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("expected-nonblank-string")
    return value


class SourceLifecycleEvent(BaseModel):
    """The exact, replayable state transition for one registered Source."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    node_id: str
    reason: str
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool

    @field_validator("node_id", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _required_text(value)


class _LifecycleNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["source", "memory", "topic"]
    reference: LifecycleReference
    parents: tuple[str, ...] = ()
    policy: Literal["all", "any"] | None = None
    revision: StrictInt | None = Field(default=None, gt=0)
    available: bool = True
    event: SourceLifecycleEvent | None = None

    def binding(self) -> dict[str, object]:
        return self.model_dump(exclude={"available", "event"})


class _LifecycleState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["powercontext.source-lifecycle.v1"] = "powercontext.source-lifecycle.v1"
    nodes: dict[str, _LifecycleNode] = Field(default_factory=dict)


class ScopedSourceLifecycle:
    """Own lifecycle rules within one runtime scope and database transaction."""

    def __init__(
        self,
        runtime: BuiltinRuntime,
        scope_id: str,
        *,
        database: AsyncDatabase,
        sources: SourceRepository,
        topics: TopicMemoryRepository,
        memory_service: Callable[[AsyncConnection], MemoryService],
        memory_artifact_id: str,
    ) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)
        self._database = database
        self._sources = sources
        self._topics = topics
        self._memory_service = memory_service
        self._memory_artifact_id = memory_artifact_id
        self._repository = SourceLifecycleRepository()

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("expected-source-reference")
        if revision is None:
            raise SourceLifecycleError("invalid-source-revision")
        try:
            node = _LifecycleNode(kind="source", reference=source_ref, revision=revision)
        except ValidationError as error:
            raise SourceLifecycleError("invalid-source-revision") from error
        await self._register(node_id, node)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("expected-memory-citation")
        await self._register(node_id, self._derived_node("memory", citation, parents, policy))

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("expected-topic-reference")
        await self._register(node_id, self._derived_node("topic", artifact_ref, parents, policy))

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("expected-lifecycle-event")
        async with self._operation() as (connection, state):
            source = state.nodes.get(event.node_id)
            if source is None or source.kind != "source":
                raise SourceLifecycleError("unknown-source")
            if event.revision != source.revision:
                raise SourceLifecycleError("revision-mismatch")
            previous = source.event
            if previous is not None:
                if event.sequence < previous.sequence:
                    raise SourceLifecycleError("stale-sequence")
                if event.sequence == previous.sequence:
                    if event != previous:
                        raise SourceLifecycleError("sequence-conflict")
                    return self._active_ids(state)
            source.available = event.available
            source.event = event
            self._propagate(state)
            await self._synchronize(connection, state, reason=event.reason)
            await self._save(connection, state)
            return self._active_ids(state)

    async def resolve(self, node_id: str, *, purpose: str) -> LifecycleReference:
        _required_text(node_id)
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("unknown-purpose")
        async with self._operation() as (_, state):
            node = state.nodes.get(node_id)
            if node is None:
                raise SourceLifecycleError("unknown-node")
            if purpose == "generation" and not node.available:
                raise SourceLifecycleError("unavailable-node")
            return node.reference

    async def _register(self, node_id: str, node: _LifecycleNode) -> None:
        _required_text(node_id)
        async with self._operation() as (connection, state):
            if node_id in node.parents:
                raise SourceLifecycleError("self-dependency")
            if any(parent not in state.nodes for parent in node.parents):
                raise SourceLifecycleError("unknown-parent")
            existing = state.nodes.get(node_id)
            if existing is not None and existing.binding() != node.binding():
                raise SourceLifecycleError("binding-conflict")
            await self._validate_reference(connection, node)
            if existing is not None:
                return
            if node.parents:
                supported = (state.nodes[parent].available for parent in node.parents)
                node.available = all(supported) if node.policy == "all" else any(supported)
            state.nodes[node_id] = node
            # New nodes can only point to existing nodes, so adding one cannot
            # close a cycle. Existing nodes cannot be rebound.
            await self._synchronize(connection, state, reason="Source lifecycle registration")
            await self._save(connection, state)

    @staticmethod
    def _derived_node(
        kind: Literal["memory", "topic"],
        reference: MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: str,
    ) -> _LifecycleNode:
        if isinstance(parents, str | bytes) or not parents:
            raise SourceLifecycleError("expected-parent-nodes")
        normalized = tuple(_required_text(parent) for parent in parents)
        if len(set(normalized)) != len(normalized):
            raise SourceLifecycleError("duplicate-parent")
        if policy not in ("all", "any"):
            raise SourceLifecycleError("unknown-policy")
        return _LifecycleNode.model_validate({
            "kind": kind,
            "reference": reference,
            "parents": tuple(sorted(normalized)),
            "policy": policy,
        })

    async def _validate_reference(self, connection: AsyncConnection, node: _LifecycleNode) -> None:
        reference = node.reference
        try:
            if isinstance(reference, SourceRef):
                await self._sources.get(connection, self.scope_id, reference)
            elif isinstance(reference, MemoryCitation):
                if (
                    reference.memory_ref.family != "memory"
                    or reference.memory_ref.artifact_id != self._memory_artifact_id
                ):
                    raise SourceLifecycleError("memory-outside-scope")
                await self._memory_service(connection).validate_citation(reference)
            else:
                await self._topics.get_exact(connection, self.scope_id, reference)
        except (
            RepositoryNotFoundError,
            ArtifactNotFoundError,
            MemoryEntryNotFoundError,
            InvalidMemoryCitationError,
        ) as error:
            raise SourceLifecycleError("unreadable-reference") from error

    @staticmethod
    def _active_ids(state: _LifecycleState) -> list[str]:
        return sorted(node_id for node_id, node in state.nodes.items() if node.available)

    @staticmethod
    def _propagate(state: _LifecycleState) -> None:
        graph = {node_id: node.parents for node_id, node in state.nodes.items()}
        if any(parent not in graph for parents in graph.values() for parent in parents):
            raise SourceLifecycleError("unknown-stored-parent")
        try:
            order = TopologicalSorter(graph).static_order()
            for node_id in order:
                node = state.nodes[node_id]
                if node.kind == "source":
                    continue
                supported = (state.nodes[parent].available for parent in node.parents)
                node.available = all(supported) if node.policy == "all" else any(supported)
        except CycleError as error:
            raise SourceLifecycleError("dependency-cycle") from error

    async def _synchronize(self, connection: AsyncConnection, state: _LifecycleState, *, reason: str) -> None:
        targets: dict[str, tuple[str, bool]] = {}
        topics: dict[tuple[str, str, int], bool] = {}
        for node in state.nodes.values():
            reference = node.reference
            if isinstance(reference, MemoryCitation):
                entry_id = reference.entry_id
                previous = targets.get(entry_id)
                if previous is not None and previous[0] != reference.entry_version_id:
                    raise SourceLifecycleError("conflicting-body-versions")
                targets[entry_id] = (
                    reference.entry_version_id,
                    node.available or (previous is not None and previous[1]),
                )
            elif isinstance(reference, ArtifactRef):
                key = (reference.family, reference.artifact_id, reference.revision)
                topics[key] = node.available or topics.get(key, False)
        if targets:
            # This service and its projection cache belong only to this transaction.
            # A rollback cannot leave a cached, uncommitted Head in the runtime.
            service = self._memory_service(connection)
            current = await service.head(self._memory_artifact_id)
            entries = {entry.entry_id: entry for entry in await service.entries(current)}
            manifest = {entry.entry_id: entry for entry in current.content.manifest.entries}
            for entry_id, (version_id, available) in sorted(targets.items()):
                entry = entries.get(entry_id)
                item = manifest.get(entry_id)
                if entry is None or item is None or entry.entry_version_id != version_id:
                    raise SourceLifecycleError("bound-body-version-changed")
                desired = "active" if available else "inactive"
                if item.state == desired:
                    continue
                transition = service.reactivate if available else service.forget
                current = await transition(current, entries=(entry,), reason=reason)
        unavailable = tuple(
            ArtifactRef(family=family, artifact_id=artifact_id, revision=revision)
            for (family, artifact_id, revision), available in topics.items()
            if not available
        )
        await self._repository.set_unavailable_topics(connection, self.scope_id, unavailable)

    async def _save(self, connection: AsyncConnection, state: _LifecycleState) -> None:
        await self._repository.save(connection, self.scope_id, state.model_dump_json().encode("utf-8"))

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[tuple[AsyncConnection, _LifecycleState]]:
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            self._database.transaction() as connection,
        ):
            payload = await self._repository.load(connection, self.scope_id)
            try:
                state = _LifecycleState() if payload is None else _LifecycleState.model_validate_json(payload)
            except ValidationError as error:
                raise SourceLifecycleError("invalid-stored-state") from error
            yield connection, state


SourceLifecycleFactory: TypeAlias = Callable[["BuiltinRuntime", str], ScopedSourceLifecycle]


class SourceLifecycleApplication:
    """Select a scoped service while preserving the runtime operation lifecycle."""

    def __init__(self, runtime: BuiltinRuntime, factory: SourceLifecycleFactory | None) -> None:
        self._runtime = runtime
        self._factory = factory

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        scope = validate_scope_id(scope_id)
        if self._factory is None:
            raise SourceLifecycleError("service-unavailable")
        return self._factory(self._runtime, scope)

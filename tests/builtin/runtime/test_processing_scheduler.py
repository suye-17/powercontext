# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import timedelta

import pytest
from sqlalchemy import select

from powercontext import SourceMaterialization
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingLeaseRepository,
)
from powercontext.builtin.persistence.tables import SHARED_TABLES, SOURCE_JOURNAL_HEADS_TABLE
from powercontext.builtin.runtime import artifact_processing as processing
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding, ArtifactProcessingSupervisor
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.processing_discovery import SourceProcessingPendingProvider
from powercontext.builtin.sources import (
    BUILTIN_SOURCE_REGISTRY,
    ContentSource,
    ContentSourceInternal,
    ContentSourceTarget,
    SourceCursor,
)
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME


async def _wait(predicate, timeout_seconds=5):
    async with asyncio.timeout(timeout_seconds):
        while not predicate():  # noqa: ASYNC110 - bounded observation of worker events
            await asyncio.sleep(0.005)


async def _wait_scan_finished(database, binding):
    # Workers can finish before discovery commits its final empty page.
    async with asyncio.timeout(5):
        while True:
            async with database.transaction() as connection:
                state = await ArtifactProcessingBindingStateRepository().load(connection, binding)
            if state is not None and not state.scan_in_progress:
                return state
            await asyncio.sleep(0.005)


async def _request(database, binding, scope, *, dirty=True, requested=True):
    intents = ArtifactProcessingIntentRepository()
    async with database.transaction() as connection:
        if dirty:
            await intents.mark_dirty(connection, scope, binding)
        if requested:
            return await intents.request(connection, scope, binding)
        return await intents.load(connection, scope, binding)


async def _intent(database, binding, scope):
    async with database.transaction() as connection:
        return await ArtifactProcessingIntentRepository().load(connection, scope, binding)


async def _capture_sources(database, scope, count, *, start=0):
    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    async with database.transaction() as connection:
        for number in range(start, start + count):
            await sources.add(
                connection,
                scope,
                ContentSource(
                    name=f"source-{number}",
                    materialization=SourceMaterialization.CAPTURED,
                    content=f"persisted source {number}",
                ),
            )


async def _save_cursor(database, scope, binding, sequence):
    async with database.transaction() as connection:
        await SourceCursorRepository().save(
            connection, scope, binding, SourceCursor(sequence=sequence), expected_generation=None
        )


async def _advance_source_cursor(database, scope, binding, *, count=None):
    async with database.transaction() as connection:
        head = await connection.scalar(
            select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope)
        )
        current = await SourceCursorRepository().load(connection, scope, binding)
        after = 0 if current is None else current.cursor.sequence
        sequence = int(head or 0) if count is None else min(int(head or 0), after + count)
        await SourceCursorRepository().save(
            connection,
            scope,
            binding,
            SourceCursor(sequence=sequence),
            expected_generation=None if current is None else current.generation,
        )
        return sequence, int(head or 0)


class _Launcher:
    def __init__(self, database, *, on_work=None, gates=None):
        self.database = database
        self.on_work = on_work
        self.gates = {} if gates is None else gates
        self.assignments = []
        self.active = set()
        self.maximum_active = 0
        self.terminated = []
        self.remaining_work = False
        self.advance_cursor = False
        self.cursor_advance_limit: int | None = None

    async def start(self, assignment):
        key = (assignment.binding_name, assignment.scope_id)
        assert key not in self.active
        self.assignments.append(assignment)
        self.active.add(key)
        self.maximum_active = max(self.maximum_active, len(self.active))
        return _Handle(self, assignment)


class _Handle:
    def __init__(self, launcher, assignment):
        self.launcher = launcher
        self.assignment = assignment

    async def wait(self):
        work = self.assignment
        try:
            gate = self.launcher.gates.get(work.scope_id)
            if gate is not None:
                await gate.wait()
            if self.launcher.on_work is not None:
                completion = await self.launcher.on_work(work)
                if completion is not None:
                    return completion
            if self.launcher.advance_cursor:
                sequence, head = await _advance_source_cursor(
                    self.launcher.database,
                    work.scope_id,
                    work.binding_name,
                    count=self.launcher.cursor_advance_limit,
                )
                self.launcher.remaining_work = sequence < head
            async with self.launcher.database.transaction() as connection:
                await ArtifactProcessingLeaseRepository().require_fence(connection, work.fence)
                intents = ArtifactProcessingIntentRepository()
                row = await intents.load(connection, work.scope_id, work.binding_name, for_update=True)
                assert row is not None
                await intents.acknowledge(
                    connection,
                    work.scope_id,
                    work.binding_name,
                    work.claimed_request_generation,
                    clean_generation=None if self.launcher.remaining_work else row.dirty_generation,
                )
            self.launcher.remaining_work = False
            return ArtifactProcessingWorkerCompletion()
        finally:
            self.launcher.active.discard((work.binding_name, work.scope_id))

    async def terminate(self):
        self.launcher.terminated.append(self.assignment.scope_id)
        self.launcher.active.discard((self.assignment.binding_name, self.assignment.scope_id))


class _ObservedIntents(ArtifactProcessingIntentRepository):
    def __init__(self):
        self.scans = []

    async def scan(self, connection, binding_name, /, **kwargs):
        self.scans.append((binding_name, dict(kwargs)))
        return await super().scan(connection, binding_name, **kwargs)


class _CycleSupervisor(ArtifactProcessingSupervisor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cycles = 0
        self.max_resident = 0

    async def __aenter__(self):
        await self.start()
        return self

    async def _cycle(self):
        self.cycles += 1
        await super()._cycle()
        for state in self.family_status.values():
            resident = int(state["retry_wait"]) + int(state["ready"]) + int(state["used_workers"])
            self.max_resident = max(self.max_resident, resident)


def _profile(tmp_path, name):
    return SQLiteProfile.open(
        SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / (name + '.db')}"), tables=SHARED_TABLES
    )


def test_each_family_has_independent_capacity_and_a_scope_is_single_flight(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "family-capacity") as profile:
            blocked = asyncio.Event()
            memory = _Launcher(profile.database, gates={"a": blocked, "b": blocked, "c": blocked})
            topic = _Launcher(profile.database)
            for scope in ("a", "b", "c"):
                await _request(profile.database, "memory-binding", scope)
            await _request(profile.database, "topic-binding", "peer")
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding("memory-binding", "memory", memory, max_workers=2),
                    ArtifactProcessingBinding("topic-binding", "topic-memory", topic, max_workers=1),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["topic-memory"]["completed"] == 1)
                assert len(memory.assignments) == 2 and memory.maximum_active == 2
                assert supervisor.family_status["memory"]["used_workers"] == 2
                assert (await _intent(profile.database, "topic-binding", "peer")).handled_generation == 1
                # A second accepted request coalesces behind the active Scope.
                await _request(profile.database, "memory-binding", "a", dirty=False)
                supervisor.wake("memory-binding")
                await asyncio.sleep(0.03)
                assert [work.scope_id for work in memory.assignments].count("a") == 1
                blocked.set()
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 4)
                assert memory.maximum_active == 2
                assert (await _intent(profile.database, "memory-binding", "a")).handled_generation == 2

    asyncio.run(scenario())


def test_success_without_durable_acknowledgement_is_a_failure_and_retries(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "no-ack") as profile:
            await _request(profile.database, "memory-binding", "scope")
            attempts = 0

            async def forget_once(_work):
                nonlocal attempts
                attempts += 1
                return ArtifactProcessingWorkerCompletion() if attempts == 1 else None

            launcher = _Launcher(profile.database, on_work=forget_once)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
                retry_base_seconds=0.15,
                retry_cap_seconds=0.15,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1)
                assert (await _intent(profile.database, "memory-binding", "scope")).handled_generation == 0
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                assert attempts == 2
                assert (await _intent(profile.database, "memory-binding", "scope")).handled_generation == 1

    asyncio.run(scenario())


def test_retry_backoff_is_quiet_and_failure_does_not_starve_other_scopes(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        async with _profile(tmp_path, "fair-retry") as profile:
            for scope in ("bad", "a", "b", "c", "d", "e"):
                await _request(profile.database, "memory-binding", scope)

            async def fail_bad(work):
                if work.scope_id == "bad":
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=fail_bad)
            async with _CycleSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
                retry_base_seconds=1,
                retry_cap_seconds=1,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 5)
                assert Counter(work.scope_id for work in launcher.assignments)["bad"] == 1
                before = supervisor.cycles
                await asyncio.sleep(0.1)
                assert supervisor.cycles - before <= 2
                assert (await _intent(profile.database, "memory-binding", "bad")).handled_generation == 0

    asyncio.run(scenario())


def test_notification_during_requested_scan_revisits_a_scope_behind_frontier(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        async with _profile(tmp_path, "notification") as profile:
            await _request(profile.database, "memory-binding", "first", requested=False)
            for scope in ("b", "c", "d", "e"):
                await _request(profile.database, "memory-binding", scope)
            gate = asyncio.Event()
            launcher = _Launcher(profile.database, gates={"b": gate})
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: len(launcher.assignments) == 1)
                await _request(profile.database, "memory-binding", "first", dirty=False)
                supervisor.wake("memory-binding")
                gate.set()
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 5)
                assert (await _intent(profile.database, "memory-binding", "first")).handled_generation == 1

    asyncio.run(scenario())


def test_automatic_scan_is_bounded_and_excludes_new_registration_until_next_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        async with _profile(tmp_path, "automatic") as profile:
            for scope in ("a", "b", "c", "d", "e"):
                await _request(profile.database, "memory-binding", scope, requested=False)
            gate = asyncio.Event()
            launcher = _Launcher(profile.database, gates={"a": gate})
            observed = _ObservedIntents()
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding", "memory", launcher, automatic_processing_interval=timedelta(hours=1)
                    ),
                ),
                lease_mode="single-process",
                intents=observed,
            ) as supervisor:
                await _wait(lambda: bool(launcher.assignments))
                async with profile.database.transaction() as connection:
                    state = await ArtifactProcessingBindingStateRepository().load(connection, "memory-binding")
                assert state is not None
                initial_checkpoint = state.last_schedule_checkpoint_at
                assert state.scan_in_progress
                await _request(profile.database, "memory-binding", "late", requested=False)
                supervisor.wake("memory-binding")
                gate.set()
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 5)
                state = await _wait_scan_finished(profile.database, "memory-binding")
                assert state is not None
                assert not state.scan_in_progress and state.last_schedule_checkpoint_at == initial_checkpoint
                assert {work.scope_id for work in launcher.assignments} == {"a", "b", "c", "d", "e"}
                assert (await _intent(profile.database, "memory-binding", "late")).requested_generation == 0
                assert all(options["limit"] <= 2 for _, options in observed.scans)

    asyncio.run(scenario())


def test_worker_timeout_reclaims_slot_and_preserves_durable_intent(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "timeout") as profile:
            await _request(profile.database, "memory-binding", "hang")
            launcher = _Launcher(profile.database, gates={"hang": asyncio.Event()})
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding("memory-binding", "memory", launcher, worker_timeout_seconds=0.05),
                ),
                lease_mode="single-process",
                retry_base_seconds=10,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["timeouts"] == 1)
                assert launcher.terminated == ["hang"]
                assert supervisor.family_status["memory"]["used_workers"] == 0
                assert (await _intent(profile.database, "memory-binding", "hang")).handled_generation == 0

    asyncio.run(scenario())


def test_restart_uses_durable_requests_and_close_revokes_the_previous_fence(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "restart") as profile:
            await _request(profile.database, "memory-binding", "scope")
            blocked = _Launcher(profile.database, gates={"scope": asyncio.Event()})
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", blocked),),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: bool(blocked.assignments))
                old_fence = supervisor.fence
                assert old_fence is not None
            assert blocked.terminated == ["scope"]
            async with profile.database.transaction() as connection:
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    await ArtifactProcessingLeaseRepository().require_fence(connection, old_fence)
            replacement = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", replacement),),
                lease_mode="single-process",
            ) as successor:
                await _wait(lambda: successor.family_status["memory"]["completed"] == 1)
                assert successor.fence is not None
                assert successor.fence.supervisor_generation > old_fence.supervisor_generation
                assert (await _intent(profile.database, "memory-binding", "scope")).handled_generation == 1

    asyncio.run(scenario())


def test_domain_control_conflict_retries_without_counting_a_provider_failure(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "conflict") as profile:
            await _request(profile.database, "memory-binding", "scope")
            attempts = 0

            async def conflict_once(_work):
                nonlocal attempts
                attempts += 1
                return (
                    ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT)
                    if attempts == 1
                    else None
                )

            launcher = _Launcher(profile.database, on_work=conflict_once)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                assert attempts == 2 and supervisor.family_status["memory"]["failed"] == 0

    asyncio.run(scenario())


def test_discovery_failure_is_confined_to_its_family(tmp_path):
    class BrokenDiscovery:
        async def reconcile(self, after_scope_id, limit, *, fence):
            raise RuntimeError

    async def scenario():
        async with _profile(tmp_path, "discovery-failure") as profile:
            await _request(profile.database, "topic-binding", "healthy")
            memory = _Launcher(profile.database)
            topic = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding("memory-binding", "memory", memory, pending_provider=BrokenDiscovery()),
                    ArtifactProcessingBinding("topic-binding", "topic-memory", topic),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["topic-memory"]["completed"] == 1)
                assert supervisor.family_status["memory"]["status"] == "degraded"
                assert supervisor.family_status["topic-memory"]["status"] == "leader"
                assert (await _intent(profile.database, "topic-binding", "healthy")).handled_generation == 1

    asyncio.run(scenario())


def test_automatic_scan_restart_reuses_frozen_members_without_double_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        async with _profile(tmp_path, "scan-restart") as profile:
            for scope in ("a", "b", "c", "d", "e"):
                await _request(profile.database, "memory-binding", scope, requested=False)
            blocked = _Launcher(profile.database, gates={"a": asyncio.Event()})
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding", "memory", blocked, automatic_processing_interval=timedelta(hours=1)
                    ),
                ),
                lease_mode="single-process",
            ):
                await _wait(lambda: bool(blocked.assignments))
            async with profile.database.transaction() as connection:
                before = await ArtifactProcessingBindingStateRepository().load(connection, "memory-binding")
            assert before is not None
            assert before.scan_in_progress
            await _request(profile.database, "memory-binding", "late", requested=False)
            recovered = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding", "memory", recovered, automatic_processing_interval=timedelta(hours=1)
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 5)
                after = await _wait_scan_finished(profile.database, "memory-binding")
                assert after is not None
                assert after.scan_generation == before.scan_generation and not after.scan_in_progress
                assert after.last_schedule_checkpoint_at == before.last_schedule_checkpoint_at
                for scope in ("a", "b", "c", "d", "e"):
                    row = await _intent(profile.database, "memory-binding", scope)
                    assert row.requested_generation == row.handled_generation == 1
                assert (await _intent(profile.database, "memory-binding", "late")).requested_generation == 0

    asyncio.run(scenario())


def test_new_requests_preserve_failure_count_until_success(tmp_path, caplog):
    async def scenario():
        async with _profile(tmp_path, "retry-generations") as profile:
            await _request(profile.database, "memory-binding", "scope")
            attempts = 0

            async def intermittent_failure(_work):
                nonlocal attempts
                attempts += 1
                if attempts in (1, 2, 4):
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=intermittent_failure)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
                retry_base_seconds=0.1,
                retry_cap_seconds=0.2,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1)
                await _request(profile.database, "memory-binding", "scope", dirty=False)
                supervisor.wake("memory-binding")
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                await _request(profile.database, "memory-binding", "scope", dirty=False)
                supervisor.wake("memory-binding")
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 2)
            retry_counts = [
                record.retry_count
                for record in caplog.records
                if getattr(record, "scope", None) == "scope" and getattr(record, "stage", None) == "worker"
            ]
            assert retry_counts == [1, 2, 1]

    asyncio.run(scenario())


def test_retry_residency_budget_serves_healthy_suffix_after_large_failure_population(tmp_path, caplog):
    """Keep the inherited 1512 cache budget while requiring progress past it."""
    caplog.set_level(logging.CRITICAL, logger=processing.__name__)
    retry_budget = 1000
    ready_budget = 100
    workers = 16

    async def scenario():
        async with _profile(tmp_path, "retry-budget") as profile:
            intents = ArtifactProcessingIntentRepository()
            async with profile.database.transaction() as connection:
                for number in range(1200):
                    await intents.request(connection, f"failed-{number:04}", "memory-binding")
                await intents.request(connection, "healthy-tail", "memory-binding")

            async def permanent_failure(work):
                if work.scope_id != "healthy-tail":
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=permanent_failure)
            async with _CycleSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher, max_workers=workers),),
                lease_mode="single-process",
                retry_base_seconds=0.5,
                retry_cap_seconds=0.5,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1, timeout_seconds=60)
                assert (await _intent(profile.database, "memory-binding", "healthy-tail")).handled_generation == 1
                # Public residency counters measure the background memory
                # contract without depending on dict layout or eviction order.
                assert supervisor.max_resident <= retry_budget + ready_budget + workers

    asyncio.run(scenario())


@pytest.mark.parametrize("admission", ["requested", "automatic"])
def test_continuously_due_retries_do_not_starve_fresh_discovery(tmp_path, monkeypatch, caplog, admission):
    caplog.set_level(logging.CRITICAL, logger=processing.__name__)
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 4)
    monkeypatch.setattr(processing, "_CHANNEL_QUEUE_LIMIT", 2)

    async def scenario():
        async with _profile(tmp_path, "continuously-due") as profile:
            for number in range(12):
                await _request(profile.database, "memory-binding", f"bad-{number:02}")
            await _request(profile.database, "memory-binding", "healthy-tail", requested=admission == "requested")

            async def always_bad(work):
                if work.scope_id != "healthy-tail":
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=always_bad)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding",
                        "memory",
                        launcher,
                        max_workers=2,
                        automatic_processing_interval=timedelta(hours=1) if admission == "automatic" else None,
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=0.001,
                retry_cap_seconds=0.001,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1, timeout_seconds=3)
                assert (await _intent(profile.database, "memory-binding", "healthy-tail")).handled_generation == 1

    asyncio.run(scenario())


def test_overflow_flush_cannot_advance_retry_and_cached_failures_cannot_extend_gate(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.CRITICAL, logger=processing.__name__)
    monkeypatch.setattr(processing, "_RETRY_STATE_LIMIT", 1)
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 4)
    monkeypatch.setattr(processing, "_CHANNEL_QUEUE_LIMIT", 2)
    monkeypatch.setattr(processing, "_CONTROL_CONFLICT_RETRY_SECONDS", 0.2)

    async def scenario():
        async with _profile(tmp_path, "overflow-pressure") as profile:
            await _request(profile.database, "memory-binding", "cached")
            await _request(profile.database, "memory-binding", "overflow")
            attempts = {"cached": [], "overflow": [], "healthy-tail": []}

            async def pressure(work):
                attempts[work.scope_id].append(asyncio.get_running_loop().time())
                if work.scope_id == "cached":
                    return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT)
                if work.scope_id == "overflow":
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=pressure)
            async with _CycleSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
                retry_base_seconds=0.8,
                retry_cap_seconds=0.8,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1)
                assert supervisor.family_status["memory"]["retry_wait"] == 1
                await _request(profile.database, "memory-binding", "overflow", dirty=False)
                await _request(profile.database, "memory-binding", "healthy-tail")
                # Repeated API wakes must respect the omitted key's cooldown.
                for _ in range(4):
                    supervisor.wake("memory-binding")
                    await asyncio.sleep(0.04)
                assert len(attempts["overflow"]) == 1
                # Cached retries keep failing, but cannot keep shifting the
                # finite overflow cohort's gate beyond the original deadline.
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1, timeout_seconds=1.5)
                assert len(attempts["cached"]) >= 3
                await _wait(lambda: len(attempts["overflow"]) >= 2, timeout_seconds=1.5)
                assert attempts["overflow"][1] - attempts["overflow"][0] >= 0.78
                assert supervisor.max_resident <= 1 + 4 + 1
                assert (await _intent(profile.database, "memory-binding", "healthy-tail")).handled_generation == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(("source_count", "accepted"), [(2, False), (3, True)])
def test_count_threshold_admits_only_when_unconsumed_journal_count_reaches_threshold(tmp_path, source_count, accepted):
    async def scenario():
        async with _profile(tmp_path, f"count-{source_count}") as profile:
            await _capture_sources(profile.database, "scope", source_count)
            binding = SOURCE_WINDOW_TRIGGER_NAME
            launcher = _Launcher(profile.database)
            launcher.advance_cursor = True
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        binding,
                        "memory",
                        launcher,
                        pending_provider=SourceProcessingPendingProvider(profile.database, binding, "memory"),
                        source_count_threshold=3,
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                if accepted:
                    await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                else:
                    await asyncio.sleep(0.1)
                    assert not launcher.assignments
                intent = await _intent(profile.database, binding, "scope")
                assert intent is not None
                assert intent.requested_generation == int(accepted)
                assert (intent.dirty_generation > intent.clean_generation) is not accepted

    asyncio.run(scenario())


def test_count_threshold_uses_independent_family_cursors(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "count-family-cursors") as profile:
            await _capture_sources(profile.database, "scope", 3)
            memory_binding = SOURCE_WINDOW_TRIGGER_NAME
            topic_binding = TOPIC_MEMORY_SOURCE_WINDOW_BINDING
            await _save_cursor(profile.database, "scope", memory_binding, 1)
            memory = _Launcher(profile.database)
            topic = _Launcher(profile.database)
            memory.advance_cursor = True
            topic.advance_cursor = True
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        memory_binding,
                        "memory",
                        memory,
                        pending_provider=SourceProcessingPendingProvider(profile.database, memory_binding, "memory"),
                        source_count_threshold=3,
                    ),
                    ArtifactProcessingBinding(
                        topic_binding,
                        "topic-memory",
                        topic,
                        pending_provider=SourceProcessingPendingProvider(
                            profile.database, topic_binding, "topic-memory"
                        ),
                        source_count_threshold=3,
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["topic-memory"]["completed"] == 1)
                await asyncio.sleep(0.05)
                assert not memory.assignments
                assert (await _intent(profile.database, memory_binding, "scope")).requested_generation == 0
                assert (await _intent(profile.database, topic_binding, "scope")).handled_generation == 1

    asyncio.run(scenario())


def test_memory_single_window_rechecks_remaining_count_after_new_capture(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "count-memory-window") as profile:
            await _capture_sources(profile.database, "scope", 3)
            binding = SOURCE_WINDOW_TRIGGER_NAME
            launcher = _Launcher(profile.database)
            launcher.advance_cursor = True
            launcher.cursor_advance_limit = 1
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        binding,
                        "memory",
                        launcher,
                        pending_provider=SourceProcessingPendingProvider(profile.database, binding, "memory"),
                        source_count_threshold=3,
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                await asyncio.sleep(0.1)
                assert [work.scope_id for work in launcher.assignments] == ["scope"]
                partial = await _intent(profile.database, binding, "scope")
                assert partial is not None and partial.dirty_generation > partial.clean_generation
                await _capture_sources(profile.database, "scope", 1, start=3)
                supervisor.wake(binding)
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 2)
                assert [work.scope_id for work in launcher.assignments] == ["scope", "scope"]
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(connection, "scope", binding)
                assert cursor is not None and cursor.cursor.sequence == 2

    asyncio.run(scenario())


def test_timed_admission_remains_available_below_count_threshold(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "count-timed-or") as profile:
            await _capture_sources(profile.database, "scope", 1)
            binding = SOURCE_WINDOW_TRIGGER_NAME
            launcher = _Launcher(profile.database)
            launcher.advance_cursor = True
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        binding,
                        "memory",
                        launcher,
                        automatic_processing_interval=timedelta(hours=1),
                        pending_provider=SourceProcessingPendingProvider(profile.database, binding, "memory"),
                        source_count_threshold=3,
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                intent = await _intent(profile.database, binding, "scope")
                assert intent is not None and intent.handled_generation == 1
                async with profile.database.transaction() as connection:
                    state = await ArtifactProcessingBindingStateRepository().load(connection, binding)
                assert state is not None and state.last_schedule_checkpoint_at is not None

    asyncio.run(scenario())


def test_repeated_count_wake_reuses_one_durable_request(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "count-wake-idempotent") as profile:
            await _capture_sources(profile.database, "scope", 3)
            binding = SOURCE_WINDOW_TRIGGER_NAME
            release = asyncio.Event()
            launcher = _Launcher(profile.database, gates={"scope": release})
            launcher.advance_cursor = True
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        binding,
                        "memory",
                        launcher,
                        pending_provider=SourceProcessingPendingProvider(profile.database, binding, "memory"),
                        source_count_threshold=3,
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: len(launcher.assignments) == 1)
                for _ in range(3):
                    supervisor.wake(binding)
                await asyncio.sleep(0.05)
                intent = await _intent(profile.database, binding, "scope")
                assert intent is not None and intent.requested_generation == 1
                assert len(launcher.assignments) == 1
                release.set()
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("binding", "family"),
    [(SOURCE_WINDOW_TRIGGER_NAME, "memory"), (TOPIC_MEMORY_SOURCE_WINDOW_BINDING, "topic-memory")],
)
def test_explicit_request_bypasses_count_threshold(tmp_path, binding, family):
    async def scenario():
        async with _profile(tmp_path, f"count-explicit-{family}") as profile:
            await _capture_sources(profile.database, "scope", 1)
            launcher = _Launcher(profile.database)
            launcher.advance_cursor = True
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        binding,
                        family,
                        launcher,
                        pending_provider=SourceProcessingPendingProvider(profile.database, binding, family),
                        source_count_threshold=3,
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _request(profile.database, binding, "scope", dirty=False)
                supervisor.wake(binding)
                await _wait(lambda: supervisor.family_status[family]["completed"] == 1)
                intent = await _intent(profile.database, binding, "scope")
                assert intent is not None and intent.handled_generation == 1
                assert len(launcher.assignments) == 1

    asyncio.run(scenario())


def test_below_threshold_dirty_input_survives_supervisor_restart(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "count-below-restart") as profile:
            await _capture_sources(profile.database, "scope", 2)
            binding = SOURCE_WINDOW_TRIGGER_NAME

            def make_binding(launcher):
                return ArtifactProcessingBinding(
                    binding,
                    "memory",
                    launcher,
                    pending_provider=SourceProcessingPendingProvider(profile.database, binding, "memory"),
                    source_count_threshold=3,
                )

            first = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(make_binding(first),),
                lease_mode="single-process",
            ):
                await asyncio.sleep(0.1)
                assert not first.assignments
                pending = await _intent(profile.database, binding, "scope")
                assert pending is not None and pending.requested_generation == 0
                assert pending.dirty_generation > pending.clean_generation

            second = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(make_binding(second),),
                lease_mode="single-process",
            ):
                await asyncio.sleep(0.1)
                assert not second.assignments
                pending = await _intent(profile.database, binding, "scope")
                assert pending is not None and pending.requested_generation == 0
                assert pending.dirty_generation > pending.clean_generation

    asyncio.run(scenario())


def test_startup_count_reconciliation_covers_all_pages_and_scopes(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 1)

    async def scenario():
        async with _profile(tmp_path, "count-startup-pages") as profile:
            for scope in ("scope-a", "scope-b", "scope-c"):
                await _capture_sources(profile.database, scope, 1)
            binding = SOURCE_WINDOW_TRIGGER_NAME
            launcher = _Launcher(profile.database)
            launcher.advance_cursor = True
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        binding,
                        "memory",
                        launcher,
                        pending_provider=SourceProcessingPendingProvider(profile.database, binding, "memory"),
                        source_count_threshold=1,
                        max_workers=1,
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 3)
                assert {work.scope_id for work in launcher.assignments} == {"scope-a", "scope-b", "scope-c"}

    asyncio.run(scenario())


def test_accepted_count_request_retries_after_supervisor_restart(tmp_path, caplog):
    caplog.set_level(logging.CRITICAL, logger=processing.__name__)

    async def scenario():
        async with _profile(tmp_path, "count-accepted-restart") as profile:
            await _capture_sources(profile.database, "scope", 3)
            binding = SOURCE_WINDOW_TRIGGER_NAME
            async with profile.database.transaction() as connection:
                await connection.exec_driver_sql("""
                    CREATE TRIGGER reject_count_cursor_insert
                    BEFORE INSERT ON pc_source_cursors
                    BEGIN
                        SELECT RAISE(ABORT, 'forced cursor persistence failure');
                    END;
                """)

            def make_binding(launcher):
                return ArtifactProcessingBinding(
                    binding,
                    "memory",
                    launcher,
                    pending_provider=SourceProcessingPendingProvider(profile.database, binding, "memory"),
                    source_count_threshold=3,
                )

            async def fail_cursor_commit(work):
                async with profile.database.transaction() as connection:
                    await SourceCursorRepository().save(
                        connection,
                        work.scope_id,
                        work.binding_name,
                        SourceCursor(sequence=1),
                        expected_generation=None,
                    )

            first = _Launcher(profile.database, on_work=fail_cursor_commit)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(make_binding(first),),
                lease_mode="single-process",
                retry_base_seconds=1,
                retry_cap_seconds=1,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1)
                accepted = await _intent(profile.database, binding, "scope")
                assert accepted is not None
                assert accepted.requested_generation == 1 and accepted.handled_generation == 0
                assert accepted.dirty_generation > accepted.clean_generation
                async with profile.database.transaction() as connection:
                    assert await SourceCursorRepository().load(connection, "scope", binding) is None

        async with _profile(tmp_path, "count-accepted-restart") as profile:
            async with profile.database.transaction() as connection:
                await connection.exec_driver_sql("DROP TRIGGER reject_count_cursor_insert")

            def make_binding(launcher):
                return ArtifactProcessingBinding(
                    binding,
                    "memory",
                    launcher,
                    pending_provider=SourceProcessingPendingProvider(profile.database, binding, "memory"),
                    source_count_threshold=3,
                )

            second = _Launcher(profile.database)
            second.advance_cursor = True
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(make_binding(second),),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                recovered = await _intent(profile.database, binding, "scope")
                assert recovered is not None
                assert recovered.requested_generation == recovered.handled_generation == 1
                assert recovered.dirty_generation == recovered.clean_generation
                assert len(second.assignments) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("lineage_first", [False, True])
def test_duplicate_and_receipt_upgrade_do_not_inflate_lineage_count(tmp_path, lineage_first):
    async def scenario():
        async with _profile(tmp_path, f"count-lineage-{lineage_first}") as profile:
            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            ordinary = ContentSource(
                name="ordinary",
                materialization=SourceMaterialization.CAPTURED,
                content="ordinary source",
            )
            lineage = ContentSource(
                name="lineage",
                materialization=SourceMaterialization.CAPTURED,
                content="lineage source",
                internal=ContentSourceInternal(
                    role="lineage_only",
                    operation="artifact_create",
                    target=ContentSourceTarget(
                        scope_id="scope", family="future-family", artifact_id="artifact", revision=1
                    ),
                ),
            )
            ordered = (lineage, ordinary) if lineage_first else (ordinary, lineage)
            async with profile.database.transaction() as connection:
                inserted = [await sources.add_with_status(connection, "scope", source) for source in ordered]
                replay, replay_created = await sources.add_with_status(connection, "scope", ordinary)
                untrusted = ContentSource(
                    name="receipt",
                    materialization=SourceMaterialization.CAPTURED,
                    content="receipt source",
                )
                trusted = untrusted.model_copy(update={"handoff_receipt": True})
                receipt, receipt_created = await sources.add_with_status(connection, "scope", untrusted)
                upgraded, upgrade_created = await sources.add_with_status(connection, "scope", trusted)
            assert [stored.journal_position for stored, _created in inserted] == [1, 2]
            assert all(created for _stored, created in inserted)
            ordinary_position = (
                inserted[0][0].journal_position if not lineage_first else inserted[1][0].journal_position
            )
            assert not replay_created and replay.journal_position == ordinary_position
            assert receipt_created and not upgrade_created
            assert upgraded.journal_position == receipt.journal_position == 3
            assert isinstance(upgraded.value, ContentSource) and upgraded.value.handoff_receipt

            binding = SOURCE_WINDOW_TRIGGER_NAME
            launcher = _Launcher(profile.database)
            launcher.advance_cursor = True
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        binding,
                        "memory",
                        launcher,
                        pending_provider=SourceProcessingPendingProvider(profile.database, binding, "memory"),
                        source_count_threshold=3,
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                assert len(launcher.assignments) == 1

    asyncio.run(scenario())

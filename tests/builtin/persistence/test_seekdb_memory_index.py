"""Memory candidate behavior against a real embedded seekdb instance."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy import event

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput, MemorySearchRequest
from powercontext.builtin.artifacts.search import analyze_fts_query
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.seekdb.memory_index import SeekDBMemoryIndex
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter

pytest.importorskip("pylibseekdb")

PROFILE = EmbeddingProfile(profile_id="pc11-test", model="fixture", dimension=384)
VECTOR = (1.0,) + (0.0,) * 383


class Embedding:
    profile = PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=(VECTOR,) * len(texts))


@contextmanager
def candidates(database):
    statements = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        lowered = statement.lower()
        if lowered.lstrip().startswith("select") and ("against" in lowered or "l2_distance" in lowered):
            statements.append((statement, parameters))

    event.listen(database.engine.sync_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", record)


@pytest.fixture(scope="module")
def store():
    with TemporaryDirectory(prefix="pc11-", dir="/tmp") as directory, asyncio.Runner() as runner:
        index = SeekDBMemoryIndex(PROFILE)
        resource = SeekDBProfile.open(SeekDBConfig(path=Path(directory) / "db"), tables=BUILTIN_TABLES + index.tables)
        profile = runner.run(resource.__aenter__())
        contexts = RelationalContexts(database=profile.database, index=index, embedding_model=Embedding())

        async def initialize():
            async with profile.database.transaction() as connection:
                await index.initialize(connection)

        try:
            runner.run(initialize())
            yield runner, contexts, index
        finally:
            runner.run(contexts.aclose_usage_recorder())
            runner.run(resource.__aexit__(None, None, None))


def test_hybrid_candidates_execute_one_statement_using_both_native_indexes(store):
    runner, contexts, index = store

    async def scenario():
        context = await contexts.get("candidate-cost")
        memory = await context.artifacts.memory.remember(
            memory=None,
            entries=(MemoryEntryInput(kind="fact", text="Native hybrid search preserves memory citations."),),
            mode="append",
        )
        assert memory is not None
        request = MemorySearchRequest(
            query="hybrid search",
            analyzed_query=analyze_fts_query("hybrid search"),
            memories=(memory.as_ref(),),
            candidate_limit=3,
            mode="hybrid",
            query_vector=VECTOR,
            embedding_profile=PROFILE,
        )
        with candidates(contexts.database) as statements:
            async with contexts.database.transaction() as connection:
                result = await index.search(connection, "candidate-cost", request)
        assert len(statements) == 1
        assert len(result.fts) == len(result.vector) == 1
        assert result.fts[0].entry_version_id == result.vector[0].entry_version_id
        assert result.fts[0].memory_ref == memory.as_ref()
        statement, parameters = statements[0]
        async with contexts.database.transaction() as connection:
            plan = await connection.exec_driver_sql("EXPLAIN " + statement, parameters)
            explanation = str(plan.all()).upper()
        assert "TEXT RETRIEVAL SCAN" in explanation
        assert "VECTOR INDEX" in explanation

    runner.run(scenario())


@pytest.mark.parametrize("match", ["any", "all"])
def test_tagged_candidates_filter_before_each_channel_limit_and_use_native_indexes(store, match):
    runner, contexts, index = store

    async def scenario():
        scope = "candidate-tags-" + match
        service = (await contexts.get(scope)).artifacts.memory
        memory = await service.remember(
            memory=None,
            entries=tuple(MemoryEntryInput(kind="fact", text=f"Tagged hybrid evidence number {i}.") for i in range(12)),
            mode="append",
        )
        assert memory is not None
        entries = sorted(await service.entries(memory), key=lambda item: item.entry_id)
        selected = entries[-1]
        target = MemoryEntryTagTarget(artifact_id=memory.artifact_id, entry_id=selected.entry_id)
        empty = await contexts.records.get_tags(scope, target)
        await contexts.records.replace_tags(scope, target, ("chosen", "verified"), expected_etag=empty.etag)
        partial = entries[-2]
        partial_target = MemoryEntryTagTarget(artifact_id=memory.artifact_id, entry_id=partial.entry_id)
        empty = await contexts.records.get_tags(scope, partial_target)
        await contexts.records.replace_tags(scope, partial_target, ("chosen",), expected_etag=empty.etag)
        expected = [selected.entry_id] if match == "all" else [partial.entry_id, selected.entry_id]
        request = MemorySearchRequest(
            query="hybrid evidence",
            analyzed_query=analyze_fts_query("hybrid evidence"),
            memories=(memory.as_ref(),),
            candidate_limit=2,
            mode="hybrid",
            query_vector=VECTOR,
            embedding_profile=PROFILE,
            tag_filter=TagFilter(tags=("chosen", "verified"), match=match),
        )
        with candidates(contexts.database) as statements:
            async with contexts.database.transaction() as connection:
                result = await index.search(connection, scope, request)
        assert [hit.entry_id for hit in result.fts] == expected
        assert [hit.entry_id for hit in result.vector] == expected
        assert len(statements) == 1
        statement, parameters = statements[0]
        async with contexts.database.transaction() as connection:
            plan = await connection.exec_driver_sql("EXPLAIN " + statement, parameters)
            explanation = str(plan.all()).upper()
        assert "TEXT RETRIEVAL SCAN" in explanation
        assert "VECTOR INDEX" in explanation

    runner.run(scenario())


def test_candidate_limits_scope_artifact_filters_and_equal_distance_stability(store):
    runner, contexts, index = store

    async def scenario():
        refs = {}
        eligible_ids = set()
        for scope in ("candidate-selected", "candidate-foreign"):
            service = (await contexts.get(scope)).artifacts.memory
            memory = await service.remember(
                memory=None,
                entries=tuple(
                    MemoryEntryInput(kind="fact", text=f"Stable hybrid evidence item {i}.") for i in range(12)
                ),
                mode="append",
            )
            assert memory is not None
            refs[scope] = memory.as_ref()
            if scope == "candidate-selected":
                eligible_ids.update(entry.entry_id for entry in await service.entries(memory))
        other = RelationalContexts(
            database=contexts.database,
            index=index,
            embedding_model=Embedding(),
            memory_artifact_id="other-memory",
        )
        other_service = (await other.get("candidate-selected")).artifacts.memory
        other_memory = await other_service.remember(
            memory=None,
            entries=(MemoryEntryInput(kind="fact", text="Stable hybrid evidence foreign artifact."),),
            mode="append",
        )
        assert other_memory is not None
        await other.aclose_usage_recorder()
        request = MemorySearchRequest(
            query="hybrid evidence",
            analyzed_query=analyze_fts_query("hybrid evidence"),
            memories=(refs["candidate-selected"],),
            candidate_limit=3,
            mode="hybrid",
            query_vector=VECTOR,
            embedding_profile=PROFILE,
        )
        results = []
        for _ in range(5):
            async with contexts.database.transaction() as connection:
                result = await index.search(connection, "candidate-selected", request)
            assert len(result.fts) == len(result.vector) == 3
            assert all(hit.memory_ref == refs["candidate-selected"] for hit in result.fts + result.vector)
            assert {hit.entry_id for hit in result.fts + result.vector} <= eligible_ids
            results.append(result)
        assert all(result == results[0] for result in results)
        absent = request.model_copy(
            update={"memories": (refs["candidate-selected"].model_copy(update={"artifact_id": "absent"}),)}
        )
        # An absent Memory is rejected by the completeness contract rather than
        # exposing another artifact's candidates.
        from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError

        with pytest.raises(CapabilityNotSupportedError):
            async with contexts.database.transaction() as connection:
                await index.search(connection, "candidate-selected", absent)

    runner.run(scenario())


@pytest.mark.parametrize("mode", ["fts", "vector"])
def test_single_channel_candidates_match_the_frozen_strategy(store, mode):
    from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex
    from powercontext.builtin.persistence.oceanbase.memory_index import (
        OceanBaseMemoryFTSIndex,
        OceanBaseMemoryVectorIndex,
    )

    runner, contexts, index = store

    async def scenario():
        scope = "single-channel-" + mode
        service = (await contexts.get(scope)).artifacts.memory
        memory = await service.remember(
            memory=None,
            entries=tuple(
                MemoryEntryInput(kind="fact", text=f"Single channel hybrid evidence number {i}.") for i in range(6)
            ),
            mode="append",
        )
        assert memory is not None
        request = MemorySearchRequest(
            query="hybrid evidence",
            analyzed_query=analyze_fts_query("hybrid evidence"),
            memories=(memory.as_ref(),),
            candidate_limit=3,
            mode=mode,
            query_vector=VECTOR,
            embedding_profile=PROFILE,
        )
        frozen = CompositeMemoryIndex(OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(PROFILE))
        async with contexts.database.transaction() as connection:
            actual = await index.search(connection, scope, request)
            expected = await frozen.search(connection, scope, request)
        assert actual == expected
        assert len(actual.fts if mode == "fts" else actual.vector) == 3
        assert not (actual.vector if mode == "fts" else actual.fts)
        missing_lexical = request.model_copy(update={"mode": "hybrid", "analyzed_query": "unmatchedword"})
        async with contexts.database.transaction() as connection:
            channels = await index.search(connection, scope, missing_lexical)
        assert channels.fts == ()
        assert len(channels.vector) == 3

    runner.run(scenario())

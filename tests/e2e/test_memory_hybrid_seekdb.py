"""Public Memory service retrieval with a real embedded seekdb runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError, MemoryEntryInput
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from tests.builtin.persistence.test_seekdb_memory_index import Embedding, candidates

pytest.importorskip("pylibseekdb")


@pytest.fixture(scope="module")
def runtime():
    with TemporaryDirectory(prefix="pc11-e2e-", dir="/tmp") as directory, asyncio.Runner() as runner:
        resource = open_builtin_contexts(
            BuiltinConfig(database=SeekDBConfig(path=Path(directory) / "db")),
            embedding_model=Embedding(),
        )
        contexts = runner.run(resource.__aenter__())
        try:
            yield runner, contexts
        finally:
            runner.run(resource.__aexit__(None, None, None))


def test_product_hybrid_search_uses_one_candidate_statement(runtime):
    runner, contexts = runtime

    async def scenario():
        service = (await contexts.get("product-cost")).artifacts.memory
        memory = await service.remember(
            memory=None,
            entries=(MemoryEntryInput(kind="fact", text="Native hybrid evidence remains exact."),),
            mode="append",
        )
        assert memory is not None
        with candidates(contexts.database) as statements:
            result = await service.search("hybrid evidence", memories=(memory,), mode="hybrid")
        assert len(statements) == 1
        assert len(result.hits) == 1
        assert result.hits[0].matched_by == ("fts", "vector")
        assert result.hits[0].memory_ref == memory.as_ref()
        assert (await service.expand(result.hits))[0].text == result.hits[0].text

    runner.run(scenario())


def test_current_citations_survive_append_revision_deactivation_and_reactivation(runtime):
    runner, contexts = runtime

    async def scenario():
        service = (await contexts.get("product-lifecycle")).artifacts.memory
        first = await service.remember(
            memory=None,
            entries=(MemoryEntryInput(kind="fact", text="Hybrid evidence uses the original contract."),),
            mode="append",
        )
        assert first is not None
        original = (await service.entries(first))[0]
        current = await service.remember(
            memory=first,
            entries=(MemoryEntryInput(kind="fact", text="A separate administrative note."),),
            mode="append",
        )
        assert current is not None
        appended = await service.search("hybrid evidence", memories=(current,), mode="hybrid")
        hit = next(hit for hit in appended.hits if hit.entry_id == original.entry_id)
        assert hit.memory_ref == current.as_ref()
        assert hit.entry_version_id == original.entry_version_id
        assert hit.text == original.text
        with pytest.raises(CapabilityNotSupportedError, match="head"):
            await service.search("hybrid evidence", memories=(first,), mode="hybrid")
        revised = await service.remember(
            memory=current,
            entries=(MemoryEntryInput(kind="fact", text="Hybrid evidence uses the revised contract.", entry=original),),
            mode="append",
        )
        assert revised is not None
        latest = next(entry for entry in await service.entries(revised) if entry.entry_id == original.entry_id)
        assert latest.entry_version_id != original.entry_version_id
        for mode in ("fts", "vector", "hybrid", "auto"):
            result = await service.search("hybrid evidence", memories=(revised,), mode=mode)
            hit = next(hit for hit in result.hits if hit.entry_id == original.entry_id)
            assert hit.entry_version_id == latest.entry_version_id
            assert hit.memory_ref == revised.as_ref()
            assert hit.text == "Hybrid evidence uses the revised contract."
            assert (await service.expand((hit,)))[0] == latest
            assert hit.matched_by
            assert set(hit.matched_by) <= {"fts", "vector"}
        inactive = await service.forget(revised, entries=(latest,))
        hidden = await service.search("hybrid evidence", memories=(inactive,), mode="hybrid")
        assert original.entry_id not in {hit.entry_id for hit in hidden.hits}
        restored = await service.reactivate(inactive, entries=(latest,))
        result = await service.search("hybrid evidence", memories=(restored,), mode="hybrid")
        hit = next(hit for hit in result.hits if hit.entry_id == original.entry_id)
        assert hit.memory_ref == restored.as_ref()
        assert hit.entry_version_id == latest.entry_version_id
        assert (await service.expand((hit,)))[0] == latest
        identities = [
            (h.memory_ref.artifact_id, h.memory_ref.revision, h.entry_id, h.entry_version_id) for h in result.hits
        ]
        assert len(identities) == len(set(identities))
        for _ in range(3):
            again = await service.search("hybrid evidence", memories=(restored,), mode="hybrid")
            assert again.hits == result.hits

    runner.run(scenario())

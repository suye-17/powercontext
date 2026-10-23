from __future__ import annotations

import asyncio

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.models import SubmitSourceObservation
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceObservation,
    TextEvidence,
)
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowSource,
    parse_csv_document,
)
from powercontext.sources.observations import manifest_for_definition, project_source_for_transport


class _CSVMemoryCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        entries = []
        for source in request.sources:
            if not isinstance(source, SourceObservation):
                continue
            evidence = TextEvidence.model_validate(source.projection(TEXT_EVIDENCE_PROJECTION_KEY))
            entries.append(MemoryEntryInput(kind="fact", text=evidence.content, sources=(source,)))
        return tuple(entries)


def test_csv_row_observations_keep_projection_after_runtime_restart(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'csv-rows.db'}"))
        registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
        rows = parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b"id,name,code\n001,Harbor Field,US\n002,North Pier,CA\n",
            )
        )
        expected = {
            "csv:airports:001": {
                "content": '{"id":"001","name":"Harbor Field","code":"US"}',
                "metadata": {
                    "document_id": "airports",
                    "primary_key": "id",
                    "primary_key_value": "001",
                    "columns": ["id", "name", "code"],
                    "row": {"id": "001", "name": "Harbor Field", "code": "US"},
                },
            },
            "csv:airports:002": {
                "content": '{"id":"002","name":"North Pier","code":"CA"}',
                "metadata": {
                    "document_id": "airports",
                    "primary_key": "id",
                    "primary_key_value": "002",
                    "columns": ["id", "name", "code"],
                    "row": {"id": "002", "name": "North Pier", "code": "CA"},
                },
            },
        }

        async with open_builtin_contexts(config) as contexts:
            await contexts.register_source_definition(manifest_for_definition(CSV_ROW_SOURCE_DEFINITION))
            for row in rows:
                source = await registry.resolve(row)
                observation = project_source_for_transport(registry, source)
                await contexts.submit_source_observation(
                    SubmitSourceObservation(scope_id="csv-test", observation=observation)
                )

        async with open_builtin_contexts(config) as contexts:
            scope = await contexts.get("csv-test")
            persisted = await scope.sources.list()
            assert {source.name for source in persisted} == set(expected)
            for source in persisted:
                evidence = TextEvidence.model_validate(
                    scope.sources.catalog.project(source, TEXT_EVIDENCE_PROJECTION_KEY)
                )
                assert evidence.source_type == "csv-row"
                assert evidence.source_id == source.name
                assert evidence.content == expected[source.name]["content"]
                assert evidence.metadata == expected[source.name]["metadata"]

    asyncio.run(scenario())


def test_csv_row_memory_search_citation_reads_exact_original_record(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'csv-memory.db'}"))
        registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
        row = parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b"id,name,code\n001,Harbor Field,US\n",
            )
        )[0]

        async with open_builtin_contexts(
            config,
            candidate_pipeline=_CSVMemoryCandidatePipeline(),
        ) as contexts:
            await contexts.register_source_definition(manifest_for_definition(CSV_ROW_SOURCE_DEFINITION))
            source = await registry.resolve(row)
            observation = project_source_for_transport(registry, source)
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="csv-memory", observation=observation)
            )
            scope = await contexts.get("csv-memory")
            persisted = await scope.sources.list()
            assert len(persisted) == 1
            stored_source = persisted[0]
            assert isinstance(stored_source, SourceObservation)
            assert scope.sources.catalog.as_ref(stored_source) == receipt.source_ref

            memory = await scope.artifacts.memory.remember(
                memory=None,
                sources=(stored_source,),
                mode="extract",
            )
            assert memory is not None
            search = await scope.artifacts.memory.search(
                "Harbor Field",
                memories=(memory,),
                mode="fts",
            )
            assert len(search.hits) == 1
            hit = search.hits[0]
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            cited_entry = await scope.artifacts.memory.validate_citation(citation)
            assert cited_entry.sources == (receipt.source_ref,)

            payload = await scope.sources.read(stored_source)
            restored_source = CSVRowSource.model_validate(payload)
            assert await registry.read(restored_source) == row

    asyncio.run(scenario())

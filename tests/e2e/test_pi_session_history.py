"""Pi event references survive the Runtime's SQLite persistence boundary."""

from __future__ import annotations

import asyncio
import json

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CommitConnectorCheckpoint,
    SubmitSourceObservation,
    open_builtin_contexts,
)
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    ConnectorBinding,
    SourceDefinitionRegistry,
    SourceObservation,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.pi_session import PI_EVENT_SOURCE_DEFINITION, PiEventInput, PiEventSource, PiSessionConnector


def test_pi_events_persist_full_tool_result_and_exact_refs_across_restart(tmp_path) -> None:
    async def scenario() -> None:
        path = tmp_path / "pi.jsonl"
        large_output = "结果" * 20_000
        records = [
            {"type": "session", "version": 3, "id": "session-a"},
            {
                "type": "message",
                "id": "user",
                "parentId": None,
                "message": {"role": "user", "content": "Find evidence"},
            },
            {
                "type": "message",
                "id": "call",
                "parentId": "user",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "toolCall", "name": "read", "id": "call-1", "arguments": {"path": "note"}}],
                },
            },
            {
                "type": "message",
                "id": "result",
                "parentId": "call",
                "message": {
                    "role": "toolResult",
                    "toolName": "read",
                    "toolCallId": "call-1",
                    "content": [{"type": "text", "text": large_output}],
                },
            },
            {"type": "compaction", "id": "compact", "parentId": "result", "summary": "Earlier evidence"},
            {"type": "branch_summary", "id": "summary", "parentId": "compact", "summary": "Branch evidence"},
            {"type": "custom_message", "id": "custom", "parentId": "summary", "content": "Injected evidence"},
            {
                "type": "message",
                "id": "bash",
                "parentId": "custom",
                "message": {"role": "bashExecution", "command": "pwd", "output": "/project"},
            },
            {
                "type": "message",
                "id": "user-two",
                "parentId": "bash",
                "message": {"role": "user", "content": "Continue"},
            },
        ]
        path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8"
        )
        registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pi.db'}"))
        binding = ConnectorBinding(
            scope_id="scope-a", binding_id="pi-history", connector_name="pi-session-history", connector_version="1"
        )

        async def ingest(contexts, *, fail_checkpoint=False):
            class Sink:
                async def submit(self, binding, item_id, definition_name, value):
                    del item_id
                    assert definition_name == "pi-event"
                    observation = project_source_for_transport(registry, await registry.resolve(value))
                    receipt = await contexts.submit_source_observation(
                        SubmitSourceObservation(scope_id=binding.scope_id, observation=observation)
                    )
                    return receipt.source_ref

            class Checkpoints:
                async def load(self, binding):
                    return (await contexts.connector_checkpoint(binding)).checkpoint

                async def save(self, binding, checkpoint, *, expected):
                    if fail_checkpoint:
                        raise RuntimeError("checkpoint write interrupted")  # noqa: TRY003
                    await contexts.commit_connector_checkpoint(
                        CommitConnectorCheckpoint(binding=binding, checkpoint=checkpoint, expected=expected)
                    )

            return await ConnectorLifecycle(sink=Sink(), checkpoints=Checkpoints()).run(
                PiSessionConnector(path), binding
            )

        async with open_builtin_contexts(config) as contexts:
            await contexts.register_source_definition(manifest_for_definition(PI_EVENT_SOURCE_DEFINITION))
            first = await ingest(contexts)
            assert len(first.items) == 8
            refs = [item.source_ref for item in first.items]
            scope = await contexts.get("scope-a")
            entries = []
            sources = await scope.sources.list()
            for source in sources:
                assert isinstance(source, SourceObservation)
                event = PiEventSource.model_validate(source.payload).event
                entries.append(MemoryEntryInput(kind="fact", text=f"Pi evidence {event.event_id}", sources=(source,)))
            memory = await scope.artifacts.memory.remember(memory=None, sources=sources, entries=entries)
            assert memory is not None
            memory_ref = memory.as_ref()

        async with open_builtin_contexts(config) as contexts:
            replay = await ingest(contexts)
            assert replay.items == ()
            assert replay.committed_checkpoint == first.committed_checkpoint
            scope = await contexts.get("scope-a")
            memory = await scope.artifacts.memory.revision(memory_ref)
            found = await scope.artifacts.memory.search("Pi evidence", memories=(memory,), mode="fts", limit=8)
            assert len(found.hits) == 8
            cited = await scope.artifacts.memory.expand(found.hits)
            cited_refs = [ref for entry in cited for ref in entry.sources]
            assert {ref.source_id for ref in cited_refs} == {ref.source_id for ref in refs}
            expected = {record["id"]: record for record in records[1:]}
            for ref in cited_refs:
                async with contexts.database.transaction() as connection:
                    stored = await contexts.repositories.sources.get(connection, "scope-a", ref)
                assert isinstance(stored.value, SourceObservation)
                projection = TextEvidence.model_validate(
                    scope.sources.catalog.project(stored.value, TEXT_EVIDENCE_PROJECTION_KEY)
                )
                native = PiEventSource.model_validate(stored.value.payload)
                event = await registry.read(native)
                assert isinstance(event, PiEventInput)
                assert projection.source_id == ref.source_id
                assert projection.content == event.content
                assert event.record == expected[event.event_id]
                assert await scope.sources.read(await scope.sources.get(stored.value)) == stored.value.payload
                if event.event_id == "result":
                    assert large_output in projection.content

            appended = {
                "type": "message",
                "id": "followup",
                "parentId": "user-two",
                "message": {"role": "assistant", "content": "Evidence located"},
            }
            with path.open("a", encoding="utf-8") as session_file:
                session_file.write(json.dumps(appended) + "\n")
            with pytest.raises(RuntimeError, match="checkpoint write interrupted"):
                await ingest(contexts, fail_checkpoint=True)
            assert (await contexts.connector_checkpoint(binding)).checkpoint == first.committed_checkpoint

        async with open_builtin_contexts(config) as contexts:
            recovered = await ingest(contexts)
            assert len(recovered.items) == 1
            assert len(await (await contexts.get("scope-a")).sources.list()) == 9
            assert len((await ingest(contexts)).items) == 0

    asyncio.run(scenario())

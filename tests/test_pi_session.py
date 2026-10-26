"""Pi v3 history is captured as exact, branch-aware Source evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, SourceDefinitionRegistry, SourceMaterialization
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
)


def _session(*records: dict[str, object]) -> bytes:
    header = {"type": "session", "version": 3, "id": "session-一"}
    return ("\n".join(json.dumps(record, ensure_ascii=False) for record in (header, *records)) + "\n").encode()


def _message(event_id: str, parent_id: str | None, role: str, content: object) -> dict[str, object]:
    return {"type": "message", "id": event_id, "parentId": parent_id, "message": {"role": role, "content": content}}


def test_selected_branch_preserves_all_visible_events_and_raw_records() -> None:
    call = {"type": "toolCall", "id": "call-1", "name": "bash", "arguments": {"z": "中文", "a": 2}}
    result: dict[str, object] = {
        "type": "message",
        "id": "result",
        "parentId": "control",
        "message": {
            "role": "toolResult",
            "toolName": "bash",
            "toolCallId": "call-1",
            "content": [{"type": "text", "text": "line 1\nline 2"}],
        },
    }
    events = parse_pi_session(
        PiSessionInput(
            content=_session(
                _message("user", None, "user", "你好"),
                _message(
                    "assistant",
                    "user",
                    "assistant",
                    [{"type": "thinking", "thinking": "private"}, {"type": "text", "text": "Answer"}, call],
                ),
                {"type": "model_change", "id": "control", "parentId": "assistant", "modelId": "x"},
                result,
                {"type": "compaction", "id": "compact", "parentId": "result", "summary": "Old context"},
                {"type": "branch_summary", "id": "summary", "parentId": "compact", "summary": "Other branch"},
                {
                    "type": "custom_message",
                    "id": "custom",
                    "parentId": "summary",
                    "content": [{"type": "text", "text": "Injected"}, {"type": "image", "data": "ignored"}],
                },
            )
        )
    )

    assert [event.event_id for event in events] == ["user", "assistant", "result", "compact", "summary", "custom"]
    assert [event.content for event in events] == [
        "user: 你好",
        'assistant: Answer\n[tool_call bash call-1] {"a":2,"z":"中文"}',
        "toolResult: [tool_result bash call-1] line 1\nline 2",
        "compaction: Old context",
        "branch_summary: Other branch",
        "custom: Injected",
    ]
    assert events[2].record == result
    assert events[2].parent_id == "control"
    assert all(event.session_id == "session-一" for event in events)


def test_default_leaf_and_explicit_leaf_follow_only_the_selected_ancestry() -> None:
    data = _session(
        _message("root", None, "user", "root"),
        _message("left", "root", "assistant", "left"),
        _message("right", "root", "assistant", "right"),
    )
    assert [event.event_id for event in parse_pi_session(PiSessionInput(content=data))] == ["root", "right"]
    assert [event.event_id for event in parse_pi_session(PiSessionInput(content=data, leaf_id="left"))] == [
        "root",
        "left",
    ]


def test_bom_bash_execution_and_non_text_blocks_preserve_only_visible_body() -> None:
    data = b"\xef\xbb\xbf" + _session(
        {
            "type": "message",
            "id": "bash",
            "parentId": None,
            "message": {"role": "bashExecution", "command": "pwd", "output": "/project\n"},
        },
        _message("image", "bash", "user", [{"type": "image", "data": "ignored"}]),
        _message("text", "image", "user", [{"type": "text", "text": "  exact text  "}]),
    )
    events = parse_pi_session(PiSessionInput(content=data))
    assert [event.event_id for event in events] == ["bash", "text"]
    assert [event.content for event in events] == ["bashExecution: $ pwd\n/project\n", "user:   exact text  "]


def test_unicode_line_separators_inside_json_strings_are_preserved() -> None:
    body = "first\N{LINE SEPARATOR}second\N{NEXT LINE}third\N{PARAGRAPH SEPARATOR}fourth"
    events = parse_pi_session(PiSessionInput(content=_session(_message("event", None, "user", body))))
    assert len(events) == 1
    assert events[0].content == f"user: {body}"


def test_whitespace_text_is_preserved_as_original_visible_content() -> None:
    events = parse_pi_session(PiSessionInput(content=_session(_message("event", None, "user", "  \n"))))
    assert [event.content for event in events] == ["user:   \n"]


def test_image_only_tool_result_connects_the_branch_without_text_evidence() -> None:
    image_result: dict[str, object] = {
        "type": "message",
        "id": "image-result",
        "parentId": None,
        "message": {
            "role": "toolResult",
            "toolName": "read",
            "toolCallId": "call-1",
            "content": [{"type": "image", "data": "opaque"}],
        },
    }
    events = parse_pi_session(
        PiSessionInput(content=_session(image_result, _message("after", "image-result", "user", "continue")))
    )
    assert [event.event_id for event in events] == ["after"]


@pytest.mark.parametrize(
    "content,leaf_id",
    [
        (b"\xff", None),
        (b"{\n", None),
        (
            b'{"type":"session","version":3,"id":"s"}\n'
            b'{"type":"message","id":"a","parentId":null,"message":{"role":"user","content":"x"}}\n'
            b'{"type":"custom","id":"bad","parentId":null,"data":NaN}\n',
            "a",
        ),
        (
            b'{"type":"session","version":3,"id":"s"}\n'
            b'{"type":"message","id":"a","parentId":null,"message":{"role":"user","content":"\\ud800"}}\n',
            None,
        ),
        (_session(), None),
        (_session(_message("a", "missing", "user", "x")), None),
        (_session({"type": "message", "id": "a", "parentId": [], "message": {"role": "user", "content": "x"}}), None),
        (_session(_message("a", None, "user", "x"), _message("a", None, "user", "y")), None),
        (_session(_message("a", None, "user", "x")), "missing"),
        (b'{"type":"session","version":2,"id":"s"}\n', None),
        (
            b'{"type":"session","version":3.0,"id":"s"}\n'
            b'{"type":"message","id":"e","parentId":null,"message":{"role":"user","content":"hi"}}\n',
            None,
        ),
        (b'{"type":"session","version":3,"id":""}\n', None),
    ],
)
def test_invalid_session_fails_without_partial_events(content: bytes, leaf_id: str | None) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=content, leaf_id=leaf_id))


def test_pi_source_projects_complete_text_and_reads_exact_event() -> None:
    async def scenario() -> None:
        record = _message("event", None, "user", "whole text")
        event = parse_pi_session(PiSessionInput(content=_session(record)))[0]
        registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
        source = await registry.resolve(event)
        evidence = registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY)

        assert source.materialization is SourceMaterialization.CAPTURED
        assert len(source.name) <= 256
        assert evidence == {
            "source_type": "pi-event",
            "source_id": source.name,
            "content": "user: whole text",
            "metadata": {
                "session_id": "session-一",
                "event_id": "event",
                "parent_id": None,
                "event_type": "message",
                "role": "user",
            },
        }
        assert await registry.read(source) == event

    asyncio.run(scenario())


@pytest.mark.parametrize("root_id", ["root", " root "])
def test_connector_restarts_switches_branches_and_rejects_rewritten_history(tmp_path: Path, root_id: str) -> None:
    class Sink:
        def __init__(self) -> None:
            self.registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
            self.sources: dict[str, object] = {}

        async def submit(self, binding, item_id, definition_name, value):
            del binding, item_id
            source = await self.registry.resolve(value)
            assert definition_name == "pi-event"
            old = self.sources.setdefault(source.name, source)
            assert old == source
            from powercontext.sources import SourceRef

            return SourceRef(source_type=definition_name, source_id=source.name)

    class Checkpoints:
        value = None

        async def load(self, binding):
            del binding
            return self.value

        async def save(self, binding, checkpoint, *, expected):
            del binding
            assert self.value == expected
            self.value = checkpoint

    from powercontext.sources import ConnectorBinding
    from powercontext.sources.connectors import ConnectorLifecycle

    async def scenario() -> None:
        path = tmp_path / "session.jsonl"
        path.write_bytes(
            _session(_message(root_id, None, "user", "shared"), _message("left", root_id, "assistant", "left"))
        )
        sink, checkpoints = Sink(), Checkpoints()
        lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
        binding = ConnectorBinding(
            scope_id="scope", binding_id="pi", connector_name="pi-session-history", connector_version="1"
        )

        first = await lifecycle.run(PiSessionConnector(path), binding)
        assert len(first.items) == 2
        assert len((await lifecycle.run(PiSessionConnector(path), binding)).items) == 0

        path.write_bytes(
            _session(
                _message(root_id, None, "user", "shared"),
                _message("left", root_id, "assistant", "left"),
                _message("right", root_id, "assistant", "right"),
            )
        )
        switched = await lifecycle.run(PiSessionConnector(path, leaf_id="right"), binding)
        assert len(switched.items) == 1
        assert len(sink.sources) == 3
        assert len((await lifecycle.run(PiSessionConnector(path, leaf_id="left"), binding)).items) == 0

        checkpoint = checkpoints.value
        path.write_bytes(
            _session(
                _message(root_id, None, "user", "shared"),
                _message("left", root_id, "assistant", "rewritten"),
                _message("right", root_id, "assistant", "right"),
            )
        )
        with pytest.raises(ValueError):
            await lifecycle.run(PiSessionConnector(path, leaf_id="right"), binding)
        assert checkpoints.value == checkpoint
        assert len(sink.sources) == 3

        path.write_bytes(b"broken")
        with pytest.raises(ValueError):
            await lifecycle.run(PiSessionConnector(path), binding)
        assert checkpoints.value == checkpoint

        path.write_bytes(
            _session(
                _message(root_id, None, "user", "shared"),
                _message("left", root_id, "assistant", "left"),
                _message("right", root_id, "assistant", "right"),
                _message("next", "right", "user", "continued"),
            )
        )
        recovered = await lifecycle.run(PiSessionConnector(path), binding)
        assert recovered.previous_checkpoint == checkpoint
        assert len(recovered.items) == 1
        assert len(sink.sources) == 4

    asyncio.run(scenario())

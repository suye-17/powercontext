"""Capture exact visible events on a selected Pi v3 session branch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"


class PiSessionInput(BaseModel):
    """The original JSONL bytes and an optional selected leaf."""

    model_config = ConfigDict(frozen=True)

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event with its complete original JSON record."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Immutable captured Pi event and its text evidence."""

    event: PiEventInput


def _event_identity(value: PiEventInput) -> str:
    identity = json.dumps([value.session_id, value.event_id], ensure_ascii=False, separators=(",", ":"))
    return f"pi-event:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


class _PiEventAdapter:
    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(name=_event_identity(value), materialization=SourceMaterialization.CAPTURED, event=value)

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return source.event


class _PiTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        event = source.event
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=event.content,
            metadata={
                "session_id": event.session_id,
                "event_id": event.event_id,
                "parent_id": event.parent_id,
                "event_type": event.event_type,
                "role": event.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(_PiEventAdapter(), projections=(_PiTextEvidenceProjection(),))


class _ParsedSession:
    def __init__(self, session_id: str, records: dict[str, dict[str, JsonValue]], events: tuple[PiEventInput, ...]):
        self.session_id = session_id
        self.records = records
        self.events = events


def _visible_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if block.get("type") == "text" and isinstance(text, str):
            parts.append(text)
        elif block.get("type") == "toolCall":
            name, call_id = block.get("name"), block.get("id")
            if isinstance(name, str) and isinstance(call_id, str):
                arguments = json.dumps(
                    block.get("arguments", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                parts.append(f"[tool_call {name} {call_id}] {arguments}")
    return "\n".join(parts)


def _message_body(message: dict[str, JsonValue], role: str) -> str | None:
    if role == "toolResult":
        name, call_id = message.get("toolName"), message.get("toolCallId")
        if not isinstance(name, str) or not isinstance(call_id, str):
            return None
        return f"[tool_result {name} {call_id}] {_visible_text(message.get('content'))}"
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        if not isinstance(command, str):
            return None
        return f"$ {command}\n{output if isinstance(output, str) else ''}"
    return _visible_text(message.get("content"))


def _as_event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_type = record.get("type")
    role: str
    body: str | None
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        raw_role = message.get("role")
        if not isinstance(raw_role, str) or raw_role == "system":
            return None
        role = raw_role
        body = _message_body(message, role)
    elif event_type in {"compaction", "branch_summary"}:
        role = cast(str, event_type)
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else ""
    elif event_type == "custom_message":
        role = "custom"
        body = _visible_text(record.get("content"))
    else:
        return None
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=cast(str, record["id"]),
        parent_id=cast(str | None, record["parentId"]),
        event_type=cast(str, event_type),
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _session_id(record: dict[str, JsonValue]) -> str:
    if record.get("type") != "session" or record.get("version") != 3:
        raise ValueError("Pi session header must have version 3")  # noqa: TRY003
    session_id = record.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session ID must be non-empty")  # noqa: TRY003
    return session_id


def _add_record(records: dict[str, dict[str, JsonValue]], record: dict[str, JsonValue]) -> None:
    event_id, parent_id = record.get("id"), record.get("parentId")
    if not isinstance(event_id, str) or not event_id.strip() or "parentId" not in record:
        raise ValueError("Pi event requires an ID and parentId")  # noqa: TRY003
    if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in records):
        raise ValueError(f"Pi event {event_id!r} has an unknown or forward parent") from None  # noqa: TRY003
    if event_id in records:
        raise ValueError(f"duplicate Pi event ID {event_id!r}")  # noqa: TRY003
    records[event_id] = record


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"Pi JSONL contains invalid JSON constant {value}")  # noqa: TRY003


def _parse_records(content: bytes) -> tuple[str, dict[str, dict[str, JsonValue]]]:
    try:
        lines = content.decode("utf-8-sig").removesuffix("\n").split("\n")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session must be UTF-8 JSONL") from error  # noqa: TRY003
    records: dict[str, dict[str, JsonValue]] = {}
    session_id: str | None = None
    for index, line in enumerate(lines):
        try:
            record = json.loads(line, parse_constant=_reject_nonfinite_json)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid Pi JSONL record at line {index + 1}") from error  # noqa: TRY003
        if not isinstance(record, dict):
            raise ValueError("Pi JSONL records must be objects") from None  # noqa: TRY003, TRY004
        try:
            json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError(f"invalid Unicode in Pi record at line {index + 1}") from error  # noqa: TRY003
        if index == 0:
            session_id = _session_id(record)
            continue
        _add_record(records, record)
    if session_id is None:
        raise ValueError("Pi session header is missing")  # noqa: TRY003
    return session_id, records


def _parse(value: PiSessionInput) -> _ParsedSession:
    session_id, records = _parse_records(value.content)
    leaf_id = value.leaf_id if value.leaf_id is not None else next(reversed(records), None)
    if leaf_id is None:
        raise ValueError("Pi session has no leaf")  # noqa: TRY003
    if leaf_id not in records:
        raise ValueError(f"Pi leaf {leaf_id!r} does not exist")  # noqa: TRY003
    chain: list[dict[str, JsonValue]] = []
    cursor: str | None = leaf_id
    while cursor is not None:
        record = records[cursor]
        chain.append(record)
        cursor = cast(str | None, record["parentId"])
    events = tuple(event for record in reversed(chain) if (event := _as_event(session_id, record)) is not None)
    return _ParsedSession(session_id, records, events)


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Return all visible raw events on the selected ancestor path, root first."""

    return _parse(value).events


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PiSessionConnector:
    """Incrementally submit visible Pi events using the lifecycle's durable checkpoint."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        parsed = _parse(PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id))
        checkpoint = session.checkpoint
        if checkpoint is None:
            imported: dict[str, str] = {}
        elif (
            not isinstance(checkpoint, dict)
            or checkpoint.get("version") != 1
            or checkpoint.get("session_id") != parsed.session_id
            or not isinstance(checkpoint.get("events"), dict)
        ):
            raise ValueError("Pi checkpoint does not match this session")  # noqa: TRY003
        else:
            imported = checkpoint["events"].copy()
        for event_id, digest in imported.items():
            record = parsed.records.get(event_id)
            if record is None or _record_digest(record) != digest:
                raise ValueError(f"previously imported Pi event {event_id!r} changed")  # noqa: TRY003
        for event in parsed.events:
            if event.event_id not in imported:
                await session.submit(_event_identity(event), PI_EVENT_SOURCE_NAME, event)
                imported[event.event_id] = _record_digest(event.record)
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": 1, "session_id": parsed.session_id, "events": imported},
        )


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]

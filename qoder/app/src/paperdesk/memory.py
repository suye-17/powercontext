from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .models import Goal, Profile, Progress, SessionSummary

PROFILE_KIND = "paperdesk.profile"
GOAL_KIND = "paperdesk.goal"
PROGRESS_KIND = "paperdesk.progress"
SESSION_KIND = "paperdesk.session"

_ATOMIC_MEMORY_SCHEMA = "powercontext.atomic-memory.v1"
_MAX_TEXT_BYTES = 8192
_SEARCH_MODES = ("auto", "fts", "vector", "hybrid")

Transport = Callable[[str, str, dict[str, Any] | None, Mapping[str, str], float], "HTTPResponse"]


class MemoryError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: Any
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ScopeInfo:
    scope_id: str
    title: str
    summary: str
    version: int

    def as_json(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "title": self.title,
            "summary": self.summary,
            "version": self.version,
        }


@dataclass(frozen=True)
class MemoryRecord:
    artifact_id: str
    revision: int
    kind: str
    text: str
    state: str
    state_version: int
    merged_into_id: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "revision": self.revision,
            "kind": self.kind,
            "text": self.text,
            "state": self.state,
            "state_version": self.state_version,
            "merged_into_id": self.merged_into_id,
        }


@dataclass(frozen=True)
class MemoryArtifact:
    artifact_id: str
    revision: int
    content: dict[str, Any]
    etag: str

    @property
    def kind(self) -> str:
        return str(self.content.get("kind", ""))

    @property
    def text(self) -> str:
        return str(self.content.get("text", ""))


@dataclass(frozen=True)
class MemoryHit:
    record: MemoryRecord
    score: float
    matched_by: tuple[str, ...]


@dataclass(frozen=True)
class MemorySearchResult:
    requested_mode: str
    mode: str
    hits: tuple[MemoryHit, ...]
    fallback_reason: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "mode": self.mode,
            "fallback_reason": self.fallback_reason,
            "hits": [
                {
                    "score": hit.score,
                    "matched_by": list(hit.matched_by),
                    "memory": hit.record.as_json(),
                }
                for hit in self.hits
            ],
        }


@dataclass(frozen=True)
class MemoryState:
    profile: Profile
    goals: tuple[Goal, ...]
    progress: Mapping[str, Progress]
    sessions: tuple[SessionSummary, ...]
    entries: tuple[MemoryRecord, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "profile": self.profile.as_json(),
            "goals": [goal.as_json() for goal in self.goals],
            "progress": [item.as_json() for item in self.progress.values()],
            "sessions": [session.as_json() for session in self.sessions],
            "entries": [entry.as_json() for entry in self.entries],
        }


class PowerContextClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8011",
        *,
        timeout: float = 20.0,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport or _urllib_transport

    def health(self) -> bool:
        try:
            response = self._request("GET", "/health/live")
        except MemoryError:
            return False
        return response.status == 200

    def ensure_scope(
        self,
        *,
        title: str,
        summary: str,
        idempotency_key: str = "paperdesk-local-scope-v1",
    ) -> ScopeInfo:
        response = self._request(
            "POST",
            "/v1/scopes",
            {
                "title": title,
                "summary": summary,
                "idempotency_key": idempotency_key,
            },
        )
        body = _mapping(response.body)
        return ScopeInfo(
            scope_id=str(body["scope_id"]),
            title=str(body.get("title", title)),
            summary=str(body.get("summary", summary)),
            version=int(body.get("version", 1)),
        )

    def remember(
        self,
        scope_id: str,
        kind: str,
        text: str,
        *,
        reason: str | None = None,
    ) -> MemoryRecord:
        _validate_write(kind, text)
        payload: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason[:512]
        response = self._request("POST", "/v1/memory/remember", payload)
        return _first_record(response.body)

    def list_entries(
        self,
        scope_id: str,
        *,
        kind: str | None = None,
        include_inactive: bool = False,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        cursor: str | None = None
        for _page in range(10):
            payload: dict[str, Any] = {
                "scope_id": scope_id,
                "include_inactive": include_inactive,
                "limit": max(1, min(limit, 100)),
            }
            if cursor:
                payload["cursor"] = cursor
            response = self._request("POST", "/v1/memory/entries/list", payload)
            body = _mapping(response.body)
            records.extend(_record(item) for item in body.get("entries", []))
            cursor = body.get("next_cursor")
            if not cursor or len(records) >= limit:
                break
        if kind is not None:
            records = [record for record in records if record.kind == kind]
        return records[:limit]

    def search(
        self,
        scope_id: str,
        query: str,
        *,
        mode: str = "hybrid",
        limit: int = 10,
    ) -> MemorySearchResult:
        if mode not in _SEARCH_MODES:
            raise ValueError(f"unsupported search mode {mode!r}; expected one of {', '.join(_SEARCH_MODES)}")
        query = query.strip()
        if not query:
            return MemorySearchResult(requested_mode=mode, mode=mode, hits=())
        payload = {
            "scope_id": scope_id,
            "query": query,
            "mode": mode,
            "limit": max(1, min(limit, 50)),
        }
        fallback_reason: str | None = None
        try:
            response = self._request("POST", "/v1/memory/search", payload)
        except MemoryError as exc:
            if mode == "fts" or (exc.status is not None and exc.status < 500):
                raise
            fallback_reason = f"{mode} 检索不可用，已回退到 fts：{exc}"
            payload["mode"] = "fts"
            response = self._request("POST", "/v1/memory/search", payload)
        body = _mapping(response.body)
        hits = tuple(
            MemoryHit(
                record=_record(item["memory"]),
                score=float(item.get("score", 0.0)),
                matched_by=tuple(str(value) for value in item.get("matched_by", [])),
            )
            for item in body.get("hits", [])
        )
        return MemorySearchResult(
            requested_mode=mode,
            mode=str(body.get("mode", mode)),
            hits=hits,
            fallback_reason=fallback_reason,
        )

    def get_artifact(self, scope_id: str, artifact_id: str) -> MemoryArtifact:
        response = self._request(
            "GET",
            f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}",
        )
        body = _mapping(response.body)
        etag = response.headers.get("etag") or ""
        if not etag:
            raise MemoryError("PowerContext artifact response did not include an ETag")
        return MemoryArtifact(
            artifact_id=str(body["artifact_id"]),
            revision=int(body["revision"]),
            content=_mapping(body.get("content")),
            etag=etag,
        )

    def revise(
        self,
        scope_id: str,
        artifact_id: str,
        *,
        kind: str,
        text: str,
        attempts: int = 2,
    ) -> MemoryArtifact:
        _validate_write(kind, text)
        last_error: MemoryError | None = None
        for _attempt in range(max(1, attempts)):
            current = self.get_artifact(scope_id, artifact_id)
            payload = {
                "content": {
                    "schema": _ATOMIC_MEMORY_SCHEMA,
                    "kind": kind,
                    "text": text,
                }
            }
            try:
                response = self._request(
                    "PUT",
                    f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}",
                    payload,
                    headers={"If-Match": current.etag},
                )
            except MemoryError as exc:
                if exc.status == 412:
                    last_error = exc
                    continue
                raise
            body = _mapping(response.body)
            revision = int(body["revision"])
            return MemoryArtifact(
                artifact_id=str(body["artifact_id"]),
                revision=revision,
                content=_mapping(body.get("content")),
                etag=response.headers.get("etag") or f'"revision:{revision}"',
            )
        raise MemoryError(
            f"artifact revision conflict after {attempts} attempts",
            status=last_error.status if last_error else 409,
            body=last_error.body if last_error else None,
        )

    def forget(self, scope_id: str, record: MemoryRecord) -> MemoryRecord:
        response = self._request(
            "POST",
            "/v1/atomic-memory/lifecycle",
            {
                "scope_id": scope_id,
                "target": {
                    "artifact": {
                        "family": "atomic-memory",
                        "artifact_id": record.artifact_id,
                        "revision": record.revision,
                    },
                    "state_version": record.state_version,
                },
                "state": "forgotten",
            },
        )
        return _first_record(response.body)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> HTTPResponse:
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        response = self._transport(
            method,
            self.base_url + path,
            payload,
            request_headers,
            self.timeout,
        )
        if response.status >= 400:
            code, message = _error_details(response.body)
            raise MemoryError(
                f"PowerContext {method} {path} failed ({response.status}): {message}",
                status=response.status,
                code=code,
                body=response.body,
            )
        return response


def load_state(client: PowerContextClient, scope_id: str) -> MemoryState:
    entries = client.list_entries(scope_id)
    profile = Profile()
    goals: list[Goal] = []
    progress: dict[str, Progress] = {}
    sessions: list[SessionSummary] = []
    for entry in entries:
        data = _loads(entry.text)
        if data is None:
            continue
        try:
            if entry.kind == PROFILE_KIND:
                profile = Profile(
                    language=str(data.get("language", "zh")),
                    digest_sentences=int(data.get("digest_sentences", 3)),
                    citation_style=str(data.get("citation_style", "inline")),
                    weekly_minutes=int(data.get("weekly_minutes", 120)),
                )
            elif entry.kind == GOAL_KIND:
                goals.append(_goal(data))
            elif entry.kind == PROGRESS_KIND:
                item = _progress(data)
                progress[item.paper_id] = item
            elif entry.kind == SESSION_KIND:
                sessions.append(_session(data))
        except (TypeError, ValueError):
            continue
    goals.sort(key=lambda goal: goal.goal_id)
    sessions.sort(key=lambda session: session.created_at, reverse=True)
    return MemoryState(
        profile=profile,
        goals=tuple(goals),
        progress=progress,
        sessions=tuple(sessions),
        entries=tuple(entries),
    )


def save_profile(client: PowerContextClient, scope_id: str, profile: Profile) -> MemoryRecord:
    existing = _find_entry(client, scope_id, PROFILE_KIND, lambda data: True)
    return _write(
        client,
        scope_id,
        PROFILE_KIND,
        profile.as_json(),
        existing,
        reason="更新阅读偏好",
    )


def save_goal(client: PowerContextClient, scope_id: str, goal: Goal) -> MemoryRecord:
    existing = _find_entry(
        client,
        scope_id,
        GOAL_KIND,
        lambda data: data.get("goal_id") == goal.goal_id,
    )
    return _write(client, scope_id, GOAL_KIND, goal.as_json(), existing, reason="设置阅读目标")


def save_progress(client: PowerContextClient, scope_id: str, progress: Progress) -> MemoryRecord:
    existing = _find_entry(
        client,
        scope_id,
        PROGRESS_KIND,
        lambda data: data.get("paper_id") == progress.paper_id,
    )
    return _write(client, scope_id, PROGRESS_KIND, progress.as_json(), existing, reason="更新阅读进度")


def save_session(client: PowerContextClient, scope_id: str, session: SessionSummary) -> MemoryRecord:
    existing = _find_entry(
        client,
        scope_id,
        SESSION_KIND,
        lambda data: data.get("session_id") == session.session_id,
    )
    return _write(
        client,
        scope_id,
        SESSION_KIND,
        session.as_json(),
        existing,
        reason="保存阅读会话摘要",
    )


def _write(
    client: PowerContextClient,
    scope_id: str,
    kind: str,
    payload: dict[str, Any],
    existing: MemoryRecord | None,
    *,
    reason: str,
) -> MemoryRecord:
    text = json.dumps(payload, ensure_ascii=False)
    if existing is None:
        return client.remember(scope_id, kind, text, reason=reason)
    artifact = client.revise(scope_id, existing.artifact_id, kind=kind, text=text)
    return MemoryRecord(
        artifact_id=artifact.artifact_id,
        revision=artifact.revision,
        kind=artifact.kind,
        text=artifact.text,
        state="active",
        state_version=existing.state_version,
        merged_into_id=None,
    )


def _find_entry(
    client: PowerContextClient,
    scope_id: str,
    kind: str,
    predicate: Callable[[dict[str, Any]], bool],
) -> MemoryRecord | None:
    for entry in client.list_entries(scope_id, kind=kind):
        data = _loads(entry.text)
        if data is not None and predicate(data):
            return entry
    return None


def _validate_write(kind: str, text: str) -> None:
    if not kind.strip():
        raise ValueError("memory kind must not be empty")
    if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise ValueError(f"memory text exceeds {_MAX_TEXT_BYTES} UTF-8 bytes")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    raise MemoryError(f"expected a JSON object, got {type(value).__name__}")


def _record(value: Any) -> MemoryRecord:
    data = _mapping(value)
    artifact = _mapping(data["artifact"])
    return MemoryRecord(
        artifact_id=str(artifact["artifact_id"]),
        revision=int(artifact["revision"]),
        kind=str(data["kind"]),
        text=str(data["text"]),
        state=str(data.get("state", "active")),
        state_version=int(data.get("state_version", 0)),
        merged_into_id=data.get("merged_into_id"),
    )


def _first_record(body: Any) -> MemoryRecord:
    records = _mapping(body).get("records") or []
    if not records:
        raise MemoryError("PowerContext mutation returned no records")
    return _record(records[0])


def _loads(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _goal(data: dict[str, Any]) -> Goal:
    tags = data.get("tags") or []
    return Goal(
        goal_id=str(data["goal_id"]),
        text=str(data["text"]),
        tags=tuple(str(tag) for tag in tags),
        deadline=str(data["deadline"]) if data.get("deadline") else None,
        status=str(data.get("status", "active")),
    )


def _progress(data: dict[str, Any]) -> Progress:
    return Progress(
        paper_id=str(data["paper_id"]),
        status=str(data["status"]),
        last_section=str(data.get("last_section", "")),
        next_action=str(data.get("next_action", "")),
        updated_at=str(data["updated_at"]),
    )


def _session(data: dict[str, Any]) -> SessionSummary:
    return SessionSummary(
        session_id=str(data["session_id"]),
        read_papers=tuple(str(value) for value in data.get("read_papers", [])),
        questions=tuple(str(value) for value in data.get("questions", [])),
        next_steps=tuple(str(value) for value in data.get("next_steps", [])),
        created_at=str(data["created_at"]),
    )


def _error_details(body: Any) -> tuple[str | None, str]:
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            return (
                str(error.get("code")) if error.get("code") is not None else None,
                str(error.get("message") or body),
            )
        return None, str(body)
    return None, str(body)[:300]


def _urllib_transport(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: Mapping[str, str],
    timeout: float,
) -> HTTPResponse:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return HTTPResponse(
                status=response.status,
                body=_decode(raw, response.headers.get("Content-Type", "")),
                headers={key.lower(): value for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return HTTPResponse(
            status=exc.code,
            body=_decode(raw, exc.headers.get("Content-Type", "") if exc.headers else ""),
            headers={key.lower(): value for key, value in (exc.headers.items() if exc.headers else [])},
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MemoryError(f"PowerContext request failed: {exc}") from exc


def _decode(raw: bytes, content_type: str) -> Any:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower():
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


__all__ = [
    "GOAL_KIND",
    "MemoryArtifact",
    "MemoryError",
    "MemoryHit",
    "MemoryRecord",
    "MemorySearchResult",
    "MemoryState",
    "PROFILE_KIND",
    "PROGRESS_KIND",
    "PowerContextClient",
    "SESSION_KIND",
    "ScopeInfo",
    "load_state",
    "save_goal",
    "save_profile",
    "save_progress",
    "save_session",
]

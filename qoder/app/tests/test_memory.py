from __future__ import annotations

import json
from typing import Any

import pytest

from paperdesk.memory import (
    GOAL_KIND,
    PROFILE_KIND,
    PROGRESS_KIND,
    SESSION_KIND,
    HTTPResponse,
    MemoryError,
    PowerContextClient,
    load_state,
    save_goal,
    save_profile,
    save_progress,
    save_session,
)
from paperdesk.models import Goal, Profile, SessionSummary
from paperdesk.service import make_progress

SCOPE = "scp_test"


class FakePowerContext:
    def __init__(self, *, fail_hybrid_once: bool = False) -> None:
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, Any, dict[str, str]]] = []
        self.fail_hybrid_once = fail_hybrid_once
        self._next_id = 0

    def __call__(self, method, url, payload, headers, timeout) -> HTTPResponse:
        path = url.split("8011", 1)[1]
        recorded = dict(payload) if isinstance(payload, dict) else payload
        self.calls.append((method, path, recorded, dict(headers)))
        if method == "POST" and path == "/v1/scopes":
            return HTTPResponse(201, {"scope_id": SCOPE, "title": payload["title"], "summary": payload["summary"], "version": 1})
        if method == "POST" and path == "/v1/memory/remember":
            return self._remember(payload)
        if method == "POST" and path == "/v1/memory/entries/list":
            return self._list(payload)
        if method == "POST" and path == "/v1/memory/search":
            return self._search(payload)
        if method == "GET" and path.startswith("/v1/scopes/"):
            return self._get_artifact(path)
        if method == "PUT" and path.startswith("/v1/scopes/"):
            return self._revise(path, payload, headers)
        if method == "POST" and path == "/v1/atomic-memory/lifecycle":
            return self._lifecycle(payload)
        return HTTPResponse(404, {"error": {"code": "not_found", "message": path}})

    def _remember(self, payload) -> HTTPResponse:
        self._next_id += 1
        artifact_id = f"art_{self._next_id}"
        record = {
            "artifact": {"family": "atomic-memory", "artifact_id": artifact_id, "revision": 1},
            "kind": payload["kind"],
            "text": payload["text"],
            "state": "active",
            "state_version": 0,
            "merged_into_id": None,
        }
        self.artifacts[artifact_id] = {"kind": payload["kind"], "text": payload["text"], "revision": 1, "state": "active", "state_version": 0}
        self.history[artifact_id] = [{"revision": 1, "text": payload["text"]}]
        return HTTPResponse(200, {"changed": True, "records": [record]})

    def _list(self, payload) -> HTTPResponse:
        entries = []
        for artifact_id, artifact in self.artifacts.items():
            if not payload.get("include_inactive") and artifact["state"] != "active":
                continue
            entries.append(
                {
                    "artifact": {"family": "atomic-memory", "artifact_id": artifact_id, "revision": artifact["revision"]},
                    "kind": artifact["kind"],
                    "text": artifact["text"],
                    "state": artifact["state"],
                    "state_version": artifact["state_version"],
                    "merged_into_id": None,
                }
            )
        return HTTPResponse(200, {"entries": entries, "next_cursor": None})

    def _search(self, payload) -> HTTPResponse:
        if payload["mode"] == "hybrid" and self.fail_hybrid_once:
            self.fail_hybrid_once = False
            return HTTPResponse(500, {"error": {"code": "internal_error", "message": "inference unavailable"}})
        hits = [
            {
                "memory": {
                    "artifact": {"family": "atomic-memory", "artifact_id": artifact_id, "revision": artifact["revision"]},
                    "kind": artifact["kind"],
                    "text": artifact["text"],
                    "state": artifact["state"],
                    "state_version": artifact["state_version"],
                    "merged_into_id": None,
                },
                "score": 0.016,
                "matched_by": ["fts"],
            }
            for artifact_id, artifact in self.artifacts.items()
            if artifact["state"] == "active"
        ]
        return HTTPResponse(200, {"mode": "text" if payload["mode"] == "fts" else payload["mode"], "hits": hits})

    def _get_artifact(self, path: str) -> HTTPResponse:
        artifact_id = path.rsplit("/", 1)[-1]
        if artifact_id not in self.artifacts:
            return HTTPResponse(404, {"error": {"code": "not_found", "message": artifact_id}})
        artifact = self.artifacts[artifact_id]
        revision = artifact["revision"]
        return HTTPResponse(
            200,
            {
                "scope_id": SCOPE,
                "family": "atomic-memory",
                "artifact_id": artifact_id,
                "revision": revision,
                "content": {
                    "schema": "powercontext.atomic-memory.v1",
                    "kind": artifact["kind"],
                    "text": artifact["text"],
                },
            },
            {"etag": f'"revision:{revision}"'},
        )

    def _revise(self, path: str, payload, headers) -> HTTPResponse:
        artifact_id = path.rsplit("/", 1)[-1]
        artifact = self.artifacts[artifact_id]
        current_etag = f'"revision:{artifact["revision"]}"'
        if headers.get("If-Match") != current_etag:
            return HTTPResponse(412, {"error": {"code": "precondition_failed", "message": "stale etag"}})
        artifact["revision"] += 1
        artifact["kind"] = payload["content"]["kind"]
        artifact["text"] = payload["content"]["text"]
        self.history[artifact_id].append({"revision": artifact["revision"], "text": artifact["text"]})
        return HTTPResponse(
            200,
            {
                "scope_id": SCOPE,
                "family": "atomic-memory",
                "artifact_id": artifact_id,
                "revision": artifact["revision"],
                "content": payload["content"],
            },
            {"etag": f'"revision:{artifact["revision"]}"'},
        )

    def _lifecycle(self, payload) -> HTTPResponse:
        artifact_id = payload["target"]["artifact"]["artifact_id"]
        artifact = self.artifacts[artifact_id]
        artifact["state"] = "forgotten"
        artifact["state_version"] += 1
        return HTTPResponse(
            200,
            {
                "changed": True,
                "records": [
                    {
                        "artifact": {"family": "atomic-memory", "artifact_id": artifact_id, "revision": artifact["revision"]},
                        "kind": artifact["kind"],
                        "text": artifact["text"],
                        "state": artifact["state"],
                        "state_version": artifact["state_version"],
                        "merged_into_id": None,
                    }
                ],
            },
        )


@pytest.fixture
def fake() -> FakePowerContext:
    return FakePowerContext()


@pytest.fixture
def client(fake: FakePowerContext) -> PowerContextClient:
    return PowerContextClient("http://127.0.0.1:8011", transport=fake)


def test_ensure_scope_is_idempotent(client: PowerContextClient, fake: FakePowerContext) -> None:
    first = client.ensure_scope(title="PaperDesk", summary="local")
    second = client.ensure_scope(title="PaperDesk", summary="local")

    assert first == second
    assert first.scope_id == SCOPE
    assert len([call for call in fake.calls if call[1] == "/v1/scopes"]) == 2


def test_remember_and_list_by_kind(client: PowerContextClient, fake: FakePowerContext) -> None:
    stored = client.remember(SCOPE, PROFILE_KIND, '{"language":"zh"}', reason="test")
    client.remember(SCOPE, GOAL_KIND, '{"goal_id":"g1"}')

    assert stored.artifact_id == "art_1"
    assert stored.revision == 1
    assert stored.state == "active"
    assert [record.kind for record in client.list_entries(SCOPE, kind=PROFILE_KIND)] == [PROFILE_KIND]
    assert len(client.list_entries(SCOPE)) == 2

    remember_call = next(call for call in fake.calls if call[1] == "/v1/memory/remember")
    assert remember_call[2] == {
        "scope_id": SCOPE,
        "kind": PROFILE_KIND,
        "text": '{"language":"zh"}',
        "reason": "test",
    }


def test_revise_uses_current_etag_and_keeps_history(client: PowerContextClient, fake: FakePowerContext) -> None:
    stored = client.remember(SCOPE, PROFILE_KIND, '{"digest_sentences":3}')

    revised = client.revise(SCOPE, stored.artifact_id, kind=PROFILE_KIND, text='{"digest_sentences":5}')
    revised_again = client.revise(SCOPE, stored.artifact_id, kind=PROFILE_KIND, text='{"digest_sentences":7}')

    assert revised.revision == 2
    assert revised_again.revision == 3
    assert revised_again.text == '{"digest_sentences":7}'
    assert [item["revision"] for item in fake.history[stored.artifact_id]] == [1, 2, 3]

    put_calls = [call for call in fake.calls if call[0] == "PUT"]
    assert put_calls[0][3]["If-Match"] == '"revision:1"'
    assert put_calls[1][3]["If-Match"] == '"revision:2"'


def test_revise_retries_after_conflict(fake: FakePowerContext) -> None:
    client = PowerContextClient("http://127.0.0.1:8011", transport=fake)
    stored = client.remember(SCOPE, PROFILE_KIND, '{"n":1}')
    original = fake._revise

    attempts = {"count": 0}

    def conflict_once(path, payload, headers):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return HTTPResponse(412, {"error": {"code": "precondition_failed", "message": "stale"}})
        return original(path, payload, headers)

    fake._revise = conflict_once  # type: ignore[method-assign]

    revised = client.revise(SCOPE, stored.artifact_id, kind=PROFILE_KIND, text='{"n":2}')

    assert revised.revision == 2
    assert attempts["count"] == 2


def test_search_falls_back_to_fts_on_server_error() -> None:
    fake = FakePowerContext(fail_hybrid_once=True)
    client = PowerContextClient("http://127.0.0.1:8011", transport=fake)
    client.remember(SCOPE, SESSION_KIND, '{"summary":"混合检索"}')

    result = client.search(SCOPE, "检索", mode="hybrid")

    assert result.requested_mode == "hybrid"
    assert result.mode == "text"
    assert result.hits
    assert result.fallback_reason is not None
    assert "回退到 fts" in result.fallback_reason
    modes = [call[2]["mode"] for call in fake.calls if call[1] == "/v1/memory/search"]
    assert modes == ["hybrid", "fts"]


def test_high_level_state_roundtrip(fake: FakePowerContext) -> None:
    client = PowerContextClient("http://127.0.0.1:8011", transport=fake)
    client.ensure_scope(title="PaperDesk", summary="local")

    save_profile(client, SCOPE, Profile(digest_sentences=5, weekly_minutes=90))
    save_goal(client, SCOPE, Goal("goal-rag", "读完 RAG 两篇", ("rag",), None))
    save_progress(client, SCOPE, make_progress("rag-survey", status="reading", last_section="Method", next_action="Evaluation"))
    save_progress(client, SCOPE, make_progress("rag-survey", status="reading", last_section="Evaluation", next_action="限时复述"))
    save_session(
        client,
        SCOPE,
        SessionSummary("session-1", ("rag-survey",), ("rerank 有什么用？",), ("复述要点",), "2026-10-24T09:30:00+00:00"),
    )

    state = load_state(client, SCOPE)

    assert state.profile.digest_sentences == 5
    assert state.profile.weekly_minutes == 90
    assert [goal.goal_id for goal in state.goals] == ["goal-rag"]
    assert state.progress["rag-survey"].last_section == "Evaluation"
    assert state.progress["rag-survey"].next_action == "限时复述"
    assert [session.session_id for session in state.sessions] == ["session-1"]
    assert len(state.entries) == 4

    profile_artifact = next(artifact for artifact in fake.artifacts.values() if artifact["kind"] == PROFILE_KIND)
    progress_artifact = next(artifact for artifact in fake.artifacts.values() if artifact["kind"] == PROGRESS_KIND)
    assert profile_artifact["revision"] == 1
    assert progress_artifact["revision"] == 2


def test_save_session_updates_existing_session_instead_of_duplicating(fake: FakePowerContext) -> None:
    client = PowerContextClient("http://127.0.0.1:8011", transport=fake)
    session = SessionSummary(
        "session-1",
        ("rag-survey",),
        ("rerank 有什么用？",),
        ("复述要点",),
        "2026-10-24T09:30:00+00:00",
    )

    save_session(client, SCOPE, session)
    saved = save_session(client, SCOPE, session)

    entries = client.list_entries(SCOPE, kind=SESSION_KIND)
    assert len(entries) == 1
    assert saved.revision == 2
    assert load_state(client, SCOPE).sessions[0].session_id == "session-1"


def test_forget_marks_entry_inactive(fake: FakePowerContext) -> None:
    client = PowerContextClient("http://127.0.0.1:8011", transport=fake)
    stored = client.remember(SCOPE, GOAL_KIND, json.dumps({"goal_id": "g1", "text": "旧目标"}))

    forgotten = client.forget(SCOPE, stored)

    assert forgotten.state == "forgotten"
    assert client.list_entries(SCOPE) == []
    inactive = client.list_entries(SCOPE, include_inactive=True)
    assert [record.state for record in inactive] == ["forgotten"]


def test_invalid_inputs_are_rejected(client: PowerContextClient) -> None:
    with pytest.raises(ValueError, match="unsupported search mode"):
        client.search(SCOPE, "query", mode="semantic")
    with pytest.raises(ValueError, match="exceeds"):
        client.remember(SCOPE, PROFILE_KIND, "x" * 9000)
    with pytest.raises(ValueError, match="kind"):
        client.remember(SCOPE, "  ", "text")


def test_http_error_carries_status_and_code() -> None:
    def transport(method, url, payload, headers, timeout):
        return HTTPResponse(503, {"error": {"code": "inference_unavailable", "message": "down"}})

    client = PowerContextClient("http://127.0.0.1:8011", transport=transport)
    with pytest.raises(MemoryError) as raised:
        client.remember(SCOPE, PROFILE_KIND, "{}")

    assert raised.value.status == 503
    assert raised.value.code == "inference_unavailable"

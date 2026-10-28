from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class PowerContextError(RuntimeError):
    pass


class PowerContextClient:
    def __init__(self, server_url: str, timeout: float = 10.0) -> None:
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.server_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            if error.code == 404 and path == "/v1/scopes/default":
                return None
            raise PowerContextError(f"{method} {path} failed: HTTP {error.code}: {body[:300]}") from error
        except urllib.error.URLError as error:
            raise PowerContextError(
                f"PowerContext server unreachable at {self.server_url}: {error.reason}. "
                "请先启动 PowerContext server（见 sample/steps.md 步骤 2），并等待 /health/live 返回 OK。"
            ) from error

    def ensure_scope(self) -> str:
        default = self._request("GET", "/v1/scopes/default")
        if isinstance(default, dict) and default.get("scope_id"):
            return str(default["scope_id"])

        created: dict[str, Any]
        try:
            created = self._request(
                "POST",
                "/v1/scopes",
                {
                    "title": "minimax-sdd",
                    "summary": "SDD context challenge scope for the MiniMax x OceanBase contest",
                    "idempotency_key": "minimax-sdd-scope",
                },
            )
        except PowerContextError as error:
            if "HTTP 409" not in str(error):
                raise
            page = self._request("GET", "/v1/scopes?query=minimax-sdd")
            items = (page.get("items") or page.get("scopes") or []) if isinstance(page, dict) else []
            if not items:
                raise
            created = items[0]

        scope_id = str(created["scope_id"])
        self._request("PUT", "/v1/scopes/default", {"scope_id": scope_id})
        return scope_id

    def remember(self, scope_id: str, text: str, kind: str = "sdd-task-state") -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/memory/remember",
            {"scope_id": scope_id, "kind": kind, "text": text},
        )

    def search(self, scope_id: str, query: str, limit: int = 5) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/memory/search",
            {"scope_id": scope_id, "query": query, "limit": limit, "mode": "fts"},
        )

    def latest_state(self, scope_id: str, task_id: str) -> dict[str, Any]:
        result = self.search(scope_id, task_id)
        hits = result.get("hits") or []
        states: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.get("memory") or hit
            text = payload.get("text")
            if not text:
                continue
            try:
                state = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(state, dict) and state.get("task_id") == task_id:
                states.append(state)
        if not states:
            return {}
        return max(states, key=lambda state: str(state.get("updated_at", "")))

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class EmbeddingError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddingResponse:
    status: int
    body: Any


Transport = Callable[[str, dict[str, Any], dict[str, str], float], EmbeddingResponse]


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    dimension: int = 1536
    timeout: float = 30.0
    batch_size: int = 16

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EmbeddingConfig:
        values = env if env is not None else _environ()
        base_url = (values.get("PAPERDESK_EMBEDDING_BASE_URL") or values.get("MINIMAX_BASE_URL") or "").strip()
        api_key = (values.get("PAPERDESK_EMBEDDING_API_KEY") or values.get("MINIMAX_API_KEY") or "").strip()
        model = (values.get("PAPERDESK_EMBEDDING_MODEL") or "").strip()
        if not model and _is_minimax(base_url):
            model = "embo-01"
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            dimension=int(values.get("PAPERDESK_EMBEDDING_DIM") or 1536),
            timeout=float(values.get("PAPERDESK_EMBEDDING_TIMEOUT") or 30),
            batch_size=int(values.get("PAPERDESK_EMBEDDING_BATCH") or 16),
        )

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    @property
    def provider(self) -> str:
        return "minimax" if _is_minimax(self.base_url) else "openai"


class EmbeddingClient:
    def __init__(self, config: EmbeddingConfig, *, transport: Transport | None = None) -> None:
        self.config = config
        self._transport = transport or _urllib_transport

    def embed(self, texts: Sequence[str], *, kind: str = "db") -> tuple[tuple[float, ...], ...]:
        if kind not in {"db", "query"}:
            raise ValueError(f"unsupported embedding kind {kind!r}; expected db or query")
        if not self.config.available:
            raise EmbeddingError("embedding provider is not configured")
        cleaned = [text.strip() or " " for text in texts]
        if not cleaned:
            return ()
        vectors: list[tuple[float, ...]] = []
        batch_size = max(1, self.config.batch_size)
        for start in range(0, len(cleaned), batch_size):
            vectors.extend(self._embed_batch(cleaned[start : start + batch_size], kind))
        return tuple(vectors)

    def _embed_batch(self, texts: list[str], kind: str) -> list[tuple[float, ...]]:
        payload = self._payload(texts, kind)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        url = self.config.base_url.rstrip("/") + "/embeddings"
        last_error = "embedding request failed"
        for attempt in range(2):
            try:
                response = self._transport(url, payload, headers, self.config.timeout)
            except OSError as exc:
                last_error = f"embedding transport error: {exc}"
            else:
                if 200 <= response.status < 300:
                    vectors = _vectors(response.body)
                    return self._validate(vectors, len(texts))
                last_error = _error_message(response)
                if response.status not in {429, 500, 502, 503, 504}:
                    break
            if attempt == 0:
                time.sleep(0.5)
        raise EmbeddingError(last_error)

    def _payload(self, texts: list[str], kind: str) -> dict[str, Any]:
        if self.config.provider == "minimax":
            return {"model": self.config.model, "texts": texts, "type": kind}
        return {"model": self.config.model, "input": texts}

    def _validate(self, vectors: list[tuple[float, ...]], expected: int) -> list[tuple[float, ...]]:
        if len(vectors) != expected:
            raise EmbeddingError(f"embedding provider returned {len(vectors)} vectors for {expected} inputs")
        for vector in vectors:
            if not vector:
                raise EmbeddingError("embedding provider returned an empty vector")
            if self.config.dimension > 0 and len(vector) != self.config.dimension:
                raise EmbeddingError(
                    f"embedding dimension mismatch: got {len(vector)}, expected {self.config.dimension}; "
                    "set PAPERDESK_EMBEDDING_DIM to match the model"
                )
        return vectors


def _vectors(body: Any) -> list[tuple[float, ...]]:
    if not isinstance(body, Mapping):
        raise EmbeddingError("embedding response must be a JSON object")
    raw = body.get("vectors")
    if raw is None:
        data = body.get("data")
        if isinstance(data, Sequence):
            raw = [item.get("embedding") if isinstance(item, Mapping) else item for item in data]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise EmbeddingError("embedding response did not contain vectors")
    vectors: list[tuple[float, ...]] = []
    for item in raw:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)):
            raise EmbeddingError("embedding vector must be a list of numbers")
        vectors.append(tuple(float(value) for value in item))
    return vectors


def _error_message(response: EmbeddingResponse) -> str:
    body = response.body
    if isinstance(body, Mapping):
        for key in ("message", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return f"embedding request failed with HTTP {response.status}: {value}"
            if isinstance(value, Mapping) and isinstance(value.get("message"), str):
                return f"embedding request failed with HTTP {response.status}: {value['message']}"
        base = body.get("base_resp")
        if isinstance(base, Mapping) and base.get("status_msg"):
            return f"embedding request failed with HTTP {response.status}: {base['status_msg']}"
    return f"embedding request failed with HTTP {response.status}"


def _is_minimax(base_url: str) -> bool:
    lowered = base_url.lower()
    return "minimax" in lowered or "minimaxi" in lowered


def _urllib_transport(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> EmbeddingResponse:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from exc
    if "json" in content_type.lower():
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = raw.decode("utf-8", "replace")
    else:
        text = raw.decode("utf-8", "replace")
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = text
    return EmbeddingResponse(status, body)


def _environ() -> Mapping[str, str]:
    import os

    return os.environ

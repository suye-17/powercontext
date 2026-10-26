from __future__ import annotations

from typing import Any

import pytest

from paperdesk.embeddings import (
    EmbeddingClient,
    EmbeddingConfig,
    EmbeddingError,
    EmbeddingResponse,
)


class FakeTransport:
    def __init__(self, responses: list[EmbeddingResponse] | None = None, *, dimension: int = 3) -> None:
        self.responses = responses or []
        self.dimension = dimension
        self.calls: list[tuple[str, dict[str, Any], dict[str, str], float]] = []

    def __call__(self, url, payload, headers, timeout) -> EmbeddingResponse:
        self.calls.append((url, dict(payload), dict(headers), timeout))
        if self.responses:
            response = self.responses.pop(0)
            return response(url, payload, headers, timeout) if callable(response) else response
        count = len(payload.get("texts") or payload.get("input") or [])
        vectors = [[float(index + offset) for offset in range(self.dimension)] for index in range(count)]
        return EmbeddingResponse(200, {"vectors": vectors})


def minimax_config(**overrides: Any) -> EmbeddingConfig:
    values = {
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "secret",
        "model": "embo-01",
        "dimension": 3,
        "batch_size": 2,
    }
    values.update(overrides)
    return EmbeddingConfig(**values)


def test_minimax_payload_uses_texts_and_kind() -> None:
    fake = FakeTransport()
    client = EmbeddingClient(minimax_config(), transport=fake)

    vectors = client.embed(["alpha", "beta"], kind="query")

    assert len(vectors) == 2
    url, payload, headers, _timeout = fake.calls[0]
    assert url == "https://api.minimaxi.com/v1/embeddings"
    assert payload == {"model": "embo-01", "texts": ["alpha", "beta"], "type": "query"}
    assert headers["Authorization"] == "Bearer secret"


def test_openai_payload_uses_input() -> None:
    fake = FakeTransport()
    config = EmbeddingConfig(base_url="https://api.example.com/v1", api_key="key", model="text-embedding-3", dimension=3)
    client = EmbeddingClient(config, transport=fake)

    client.embed(["alpha"])

    _url, payload, _headers, _timeout = fake.calls[0]
    assert payload == {"model": "text-embedding-3", "input": ["alpha"]}


def test_batches_and_parses_openai_shape() -> None:
    fake = FakeTransport()
    client = EmbeddingClient(minimax_config(batch_size=2), transport=fake)

    def openai(count: int) -> EmbeddingResponse:
        return EmbeddingResponse(
            200,
            {"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in range(count)]},
        )

    def respond(_url, payload, _headers, _timeout) -> EmbeddingResponse:
        return openai(len(payload["texts"]))

    fake.responses = [respond, respond, respond]
    vectors = client.embed(["a", "b", "c", "d", "e"])

    assert len(vectors) == 5
    assert [len(payload["texts"]) for _url, payload, _headers, _timeout in fake.calls] == [2, 2, 1]


def test_retries_transient_failure() -> None:
    fake = FakeTransport(
        [
            EmbeddingResponse(503, {"error": {"message": "busy"}}),
            EmbeddingResponse(200, {"vectors": [[1.0, 2.0, 3.0]]}),
        ]
    )
    client = EmbeddingClient(minimax_config(), transport=fake)

    assert client.embed(["alpha"]) == ((1.0, 2.0, 3.0),)
    assert len(fake.calls) == 2


def test_rejects_dimension_mismatch() -> None:
    fake = FakeTransport(dimension=2)
    client = EmbeddingClient(minimax_config(dimension=3), transport=fake)

    with pytest.raises(EmbeddingError, match="dimension mismatch"):
        client.embed(["alpha"])


def test_rejects_client_error_without_retry() -> None:
    fake = FakeTransport([EmbeddingResponse(401, {"error": {"message": "bad key"}})])
    client = EmbeddingClient(minimax_config(), transport=fake)

    with pytest.raises(EmbeddingError, match="bad key"):
        client.embed(["alpha"])
    assert len(fake.calls) == 1


def test_unconfigured_provider_raises() -> None:
    client = EmbeddingClient(EmbeddingConfig(), transport=FakeTransport())

    with pytest.raises(EmbeddingError, match="not configured"):
        client.embed(["alpha"])


def test_from_env_prefers_paperdesk_then_minimax() -> None:
    config = EmbeddingConfig.from_env(
        {
            "MINIMAX_BASE_URL": "https://api.minimaxi.com/v1",
            "MINIMAX_API_KEY": "minimax-key",
        }
    )

    assert config.available
    assert config.provider == "minimax"
    assert config.model == "embo-01"
    assert EmbeddingConfig.from_env({}).available is False

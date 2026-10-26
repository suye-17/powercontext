"""Cross-call embedding request coalescing and bounded vector caching."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, cast

from powercontext.builtin.inference.errors import InvalidInferenceOutputError
from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, QueryEmbeddingModel

_EmbeddingKind = Literal["document", "query"]
_Key = tuple[_EmbeddingKind, str]
_DOCUMENT: _EmbeddingKind = "document"
_QUERY: _EmbeddingKind = "query"


@dataclass(slots=True)
class _TextEntry:
    key: _Key
    future: asyncio.Future[EmbeddingVector]
    batch: _Batch | None = None


@dataclass(slots=True)
class _Batch:
    kind: _EmbeddingKind
    texts: tuple[str, ...]
    entries: tuple[_TextEntry, ...]
    usage: InferenceUsage = field(default_factory=lambda: InferenceUsage(requests=0))


class CoalescingEmbeddingModel:
    """Share in-flight embedding work and cache successful vectors across callers."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if not isinstance(max_batch_size, int) or isinstance(max_batch_size, bool) or max_batch_size <= 0:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if (
            not isinstance(coalesce_ms, (int, float))
            or isinstance(coalesce_ms, bool)
            or not math.isfinite(float(coalesce_ms))
            or coalesce_ms < 0
        ):
            raise ValueError("coalesce_ms must be a non-negative finite number")  # noqa: TRY003
        if not isinstance(cache_size, int) or isinstance(cache_size, bool) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003

        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[_Key, EmbeddingVector] = OrderedDict()
        self._entries: dict[_Key, _TextEntry] = {}
        self._pending: list[_Key] = []
        self._flush_handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input text, sharing and caching work across callers."""

        return await self._embed(texts, _DOCUMENT)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return query vectors, using a separate lane when the provider distinguishes them."""

        kind = _QUERY if isinstance(self._model, QueryEmbeddingModel) else _DOCUMENT
        return await self._embed(texts, kind)

    async def _embed(self, texts: tuple[str, ...], kind: _EmbeddingKind) -> EmbeddingResult:
        unique_texts = tuple(dict.fromkeys(texts))
        if not unique_texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        keys = tuple((kind, text) for text in unique_texts)
        vectors: dict[_Key, EmbeddingVector] = {}
        entries: dict[_Key, _TextEntry] = {}
        for key in keys:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                vectors[key] = cached
                continue
            entry = self._entries.get(key)
            if entry is None:
                entry = _TextEntry(key=key, future=asyncio.get_running_loop().create_future())
                entry.future.add_done_callback(_consume_exception)
                self._entries[key] = entry
                self._pending.append(key)
                self._schedule_flush()
            entries[key] = entry

        if entries:
            waiters = tuple(asyncio.shield(entry.future) for entry in entries.values())
            for waiter in waiters:
                waiter.add_done_callback(_consume_exception)
            results = await asyncio.gather(*waiters)
            for key, vector in zip(entries, results, strict=True):
                vectors[key] = vector

        batches: dict[int, _Batch] = {}
        for entry in entries.values():
            batch = entry.batch
            if batch is not None:
                batches[id(batch)] = batch

        return EmbeddingResult(
            vectors=tuple(vectors[(kind, text)] for text in texts),
            usage=_sum_usage(batch.usage for batch in batches.values()),
        )

    def _schedule_flush(self) -> None:
        if self._flush_handle is not None:
            return
        self._flush_handle = asyncio.get_running_loop().call_later(self._coalesce_seconds, self._flush)

    def _flush(self) -> None:
        self._flush_handle = None
        if not self._pending:
            return
        pending = self._pending
        self._pending = []
        grouped: dict[_EmbeddingKind, list[_Key]] = {}
        for key in pending:
            grouped.setdefault(key[0], []).append(key)
        for kind, keys in grouped.items():
            for offset in range(0, len(keys), self._max_batch_size):
                batch_keys = tuple(keys[offset : offset + self._max_batch_size])
                texts = tuple(key[1] for key in batch_keys)
                entries = tuple(self._entries[key] for key in batch_keys)
                batch = _Batch(kind=kind, texts=texts, entries=entries)
                for entry in entries:
                    entry.batch = batch
                task = asyncio.create_task(self._run_batch(batch))
                self._tasks.add(task)
                task.add_done_callback(self._batch_done)

    async def _run_batch(self, batch: _Batch) -> None:
        try:
            result = await self._call_model(batch.kind, batch.texts)
            _validate_vectors(batch, result)
            batch.usage = result.usage
            self._store(batch, result.vectors)
        except asyncio.CancelledError:
            _cancel_entries(batch)
            raise
        except BaseException as exc:
            _fail_entries(batch, exc)
            if not isinstance(exc, Exception):
                raise
        else:
            _resolve_entries(batch, result.vectors)
        finally:
            for entry in batch.entries:
                self._entries.pop(entry.key, None)

    async def _call_model(self, kind: _EmbeddingKind, texts: tuple[str, ...]) -> EmbeddingResult:
        if kind == _QUERY:
            return await cast(QueryEmbeddingModel, self._model).embed_query(texts)
        return await self._model.embed(texts)

    def _batch_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def _store(self, batch: _Batch, vectors: tuple[EmbeddingVector, ...]) -> None:
        if self._cache_size == 0:
            return
        for entry, vector in zip(batch.entries, vectors, strict=True):
            self._cache[entry.key] = vector
            self._cache.move_to_end(entry.key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _validate_vectors(batch: _Batch, result: EmbeddingResult) -> None:
    if len(result.vectors) != len(batch.texts):
        raise InvalidInferenceOutputError("embedding", "batch-vector-count")


def _cancel_entries(batch: _Batch) -> None:
    for entry in batch.entries:
        if not entry.future.done():
            entry.future.cancel()


def _fail_entries(batch: _Batch, error: BaseException) -> None:
    for entry in batch.entries:
        if not entry.future.done():
            entry.future.set_exception(error)


def _resolve_entries(batch: _Batch, vectors: tuple[EmbeddingVector, ...]) -> None:
    for entry, vector in zip(batch.entries, vectors, strict=True):
        if not entry.future.done():
            entry.future.set_result(vector)


def _consume_exception(future: asyncio.Future[EmbeddingVector]) -> None:
    if not future.cancelled():
        future.exception()


def _sum_usage(usages: Iterable[InferenceUsage]) -> InferenceUsage:
    values = tuple(usages)
    if not values:
        return InferenceUsage(requests=0)
    return InferenceUsage(
        requests=sum(value.requests for value in values),
        input_tokens=_sum_optional(value.input_tokens for value in values),
        output_tokens=_sum_optional(value.output_tokens for value in values),
    )


def _sum_optional(values: Iterable[int | None]) -> int | None:
    present = tuple(value for value in values if value is not None)
    return None if not present else sum(present)


__all__ = ["CoalescingEmbeddingModel"]

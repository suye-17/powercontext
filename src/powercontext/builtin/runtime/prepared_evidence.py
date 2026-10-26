"""Select cited original Memory intervals using only the supplied query and candidates."""

from __future__ import annotations

import heapq
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache

from pydantic import ValidationError

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PreparedContext
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedContextFragment,
    PreparedContextOmissions,
    PreparedMemoryCandidates,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_BEGIN = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_SENTENCE_END = re.compile(r"""[.!?。！？]+["'”’)\]]*\s+|\n\s*\n|[。！？]+""")  # noqa: RUF001
_ABBREVIATIONS = ("e.g.", "i.e.", "et al.", "dr.", "fig.")
_WINDOW_LIMIT = 32
_BEAM_WIDTH = 32
_BM25_B = 0.0
_BM25_K1 = 10.0
_TF_CAP = 8
_DOCUMENT_BASE = 0.0
Span = tuple[int, int]
State = tuple[tuple[Span, ...], ...]


def _render(items: Sequence[dict[str, object]]) -> str:
    encoded = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
    return f"{TRUST_POLICY}\n\n{_BEGIN}\n{encoded}\n{_END}"


_WRAPPER_BYTES = len(_render(()).encode("utf-8"))


@dataclass(frozen=True)
class _Entry:
    address: MemoryEntryAddress
    text: str
    citation: dict[str, object]
    escaped_prefix: tuple[int, ...]
    item_overhead: int
    sentences: tuple[Span, ...]
    terms: Counter[str]
    length: int

    def item_bytes(self, span: Span) -> int:
        start, end = span
        return (
            self.item_overhead + self.escaped_prefix[end] - self.escaped_prefix[start] + len(str(start)) + len(str(end))
        )

    def item(self, span: Span) -> dict[str, object]:
        start, end = span
        return {"citation": self.citation, "content": self.text[start:end], "start": start, "end": end}


def _sentence_spans(text: str) -> tuple[Span, ...]:
    spans = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if match.group().startswith("."):
            prefix = text[max(0, match.start() - 12) : match.start() + 1].casefold()
            if prefix.endswith(_ABBREVIATIONS) or re.search(r"(?:^|\W)[a-z]\.$", prefix):
                continue
        end = match.end()
        while start < end and text[start].isspace():
            start += 1
        trimmed = end
        while trimmed > start and text[trimmed - 1].isspace():
            trimmed -= 1
        if start < trimmed:
            spans.append((start, trimmed))
        start = end
    end = len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        spans.append((start, end))
    return tuple(spans)


def _escaped_prefix(text: str) -> tuple[int, ...]:
    """Count exactly the bytes JSON uses inside a string with ensure_ascii=False."""
    result = [0]
    for char in text:
        if char in '"\\\b\f\n\r\t':
            size = 2
        elif ord(char) < 32:
            size = 6
        else:
            size = len(char.encode("utf-8"))
        result.append(result[-1] + size)
    return tuple(result)


def _entries(candidates: Sequence[PreparedMemoryCandidates]) -> tuple[_Entry, ...]:
    entries = []
    seen = set()
    # Check every anchor before relevance filtering, including duplicates and blank hits.
    for group in candidates:
        if not group.hits:
            continue
        if group.memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        if any(hit.memory_ref != group.memory_ref for hit in group.hits):
            raise PreparedContextInvariantError("memory-ref-mismatch")
        try:
            memory = ArtifactAddress(scope_id=group.scope_id, artifact=group.memory_ref)
        except ValidationError as error:
            raise PreparedContextInvariantError("memory-scope-missing") from error
        for hit in group.hits:
            address = MemoryEntryAddress(memory=memory, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id)
            identity = (
                group.scope_id,
                group.memory_ref.family,
                group.memory_ref.artifact_id,
                group.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            if identity in seen or not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            seen.add(identity)
            citation = {
                "scope_id": group.scope_id,
                "memory_ref": group.memory_ref.model_dump(mode="json"),
                "entry_id": hit.entry_id,
                "entry_version_id": hit.entry_version_id,
            }
            template = {"citation": citation, "content": "", "start": 0, "end": 0}
            overhead = len(json.dumps(template, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) - 2
            terms = Counter(analyze_text(hit.text).split())
            entries.append(
                _Entry(
                    address,
                    hit.text,
                    citation,
                    _escaped_prefix(hit.text),
                    overhead,
                    _sentence_spans(hit.text),
                    terms,
                    sum(terms.values()),
                )
            )
    return tuple(entries)


def _bm25(counts: Counter[str], length: int, average: float, weights: dict[str, float]) -> float:
    normalization = _BM25_K1 * (1 - _BM25_B + _BM25_B * length / max(1.0, average))
    return sum(
        weights[term] * min(_TF_CAP, count) * (_BM25_K1 + 1) / (min(_TF_CAP, count) + normalization)
        for term, count in counts.items()
        if term in weights
    )


def _windows(entry: _Entry, max_bytes: int) -> Iterator[Span]:
    for index, (start, end) in enumerate(entry.sentences):
        for count in (1, 2, 3):
            if index + count <= len(entry.sentences):
                yield start, entry.sentences[index + count - 1][1]
        if end - start <= 32 or _WRAPPER_BYTES + entry.item_bytes((start, end)) <= max_bytes:
            continue
        for width in (32, 64, 128, 256, 512):
            if width >= end - start:
                continue
            for offset in range(start, end - width + 1, min(16, width // 2)):
                yield offset, offset + width
            yield end - width, end


def _merge(entry: _Entry, spans: tuple[Span, ...], addition: Span) -> tuple[Span, ...]:
    merged: list[Span] = []
    for start, end in sorted((*spans, addition)):
        while merged:
            left = merged[-1]
            joined = (left[0], max(left[1], end))
            # A short gap can cost less than another complete citation. It remains original text.
            if (
                start > left[1]
                and entry.item_bytes(joined) >= entry.item_bytes(left) + entry.item_bytes((start, end)) + 1
            ):
                break
            merged.pop()
            start, end = joined
        merged.append((start, end))
    return tuple(merged)


class _Selection:
    """One bounded query-local search; caches never outlive this selection."""

    def __init__(self, entries: tuple[_Entry, ...], query_terms: set[str], max_bytes: int) -> None:
        self.entries = entries
        self.query_terms = query_terms
        self.max_bytes = max_bytes
        self.weights = {
            term: 1 + math.log((len(entries) + 1) / (sum(term in entry.terms for entry in entries) + 1))
            for term in query_terms
        }
        doc_average = sum(entry.length for entry in entries) / len(entries)
        doc_scores = [_bm25(entry.terms, entry.length, doc_average, self.weights) for entry in entries]
        top_doc = max(doc_scores, default=0.0)
        self.priors = [
            _DOCUMENT_BASE + (1 - _DOCUMENT_BASE) * score / top_doc if top_doc else _DOCUMENT_BASE
            for score in doc_scores
        ]
        self.empty: State = tuple(() for _ in entries)
        self.entry_counts: Callable[[int, tuple[Span, ...]], Counter[str]] = lru_cache(maxsize=2048)(self._entry_counts)
        self.entry_score: Callable[[int, tuple[Span, ...]], float] = lru_cache(maxsize=2048)(self._entry_score)
        self.state_bytes: Callable[[State], int] = lru_cache(maxsize=2048)(self._state_bytes)
        self.state_score: Callable[[State], float] = lru_cache(maxsize=2048)(self._state_score)
        self.rank = lru_cache(maxsize=2048)(self._rank)

    def _entry_counts(self, index: int, spans: tuple[Span, ...]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for start, end in spans:
            counts.update(
                term for term in analyze_text(self.entries[index].text[start:end]).split() if term in self.query_terms
            )
        return counts

    def _reward(self, weighted_counts: dict[str, float]) -> float:
        return math.fsum(
            self.weights[term] * (_BM25_K1 + 1) * min(_TF_CAP, count) / (_BM25_K1 + min(_TF_CAP, count))
            for term, count in weighted_counts.items()
        )

    def _entry_score(self, index: int, spans: tuple[Span, ...]) -> float:
        return self._reward({
            term: self.priors[index] * count for term, count in self.entry_counts(index, spans).items()
        })

    def _state_bytes(self, state: State) -> int:
        sizes = [entry.item_bytes(span) for entry, spans in zip(self.entries, state, strict=True) for span in spans]
        return _WRAPPER_BYTES + sum(sizes) + max(0, len(sizes) - 1)

    def _state_score(self, state: State) -> float:
        # Repeated query evidence shares one diminishing reward across all citations.
        weighted_counts: dict[str, float] = {}
        for index, spans in enumerate(state):
            for term, count in self.entry_counts(index, spans).items():
                weighted_counts[term] = weighted_counts.get(term, 0.0) + self.priors[index] * count
        return self._reward(weighted_counts)

    def _rank(self, state: State) -> tuple[float, int, int, State]:
        covered = set()
        for index, spans in enumerate(state):
            covered.update(self.entry_counts(index, spans))
        return (-self.state_score(state), -len(covered), self.state_bytes(state), state)

    def pool(self) -> list[tuple[tuple[float, float, int, int, int, int], int, Span]]:
        pool = []
        for index, entry in enumerate(self.entries):
            best = []
            best_feasible = None
            seen = set()
            for span in _windows(entry, self.max_bytes):
                if span in seen:
                    continue
                seen.add(span)
                score = self.entry_score(index, (span,))
                if score <= 0:
                    continue
                cost = entry.item_bytes(span)
                order = (score / cost, score, -cost, -index, -span[0], -span[1])
                if _WRAPPER_BYTES + cost <= self.max_bytes and (
                    best_feasible is None or (score, -cost, -span[0], -span[1]) > best_feasible[0]
                ):
                    best_feasible = ((score, -cost, -span[0], -span[1]), (order, index, span))
                if len(best) < _WINDOW_LIMIT:
                    heapq.heappush(best, (order, index, span))
                elif order > best[0][0]:
                    heapq.heapreplace(best, (order, index, span))
            # Reserve the best feasible score before density pruning can discard it.
            if best_feasible is not None and best_feasible[1] not in best:
                if len(best) >= _WINDOW_LIMIT:
                    heapq.heappop(best)
                best.append(best_feasible[1])
            pool.extend(best)
        return sorted(pool, reverse=True)

    def extend(self, state: State, index: int, span: Span) -> State:
        return (*state[:index], _merge(self.entries[index], state[index], span), *state[index + 1 :])

    def search(self, pool: Sequence[tuple[tuple[float, float, int, int, int, int], int, Span]]) -> State:
        beam = [self.empty]
        best_single = self.empty
        for _, index, span in pool:
            standalone = (*self.empty[:index], (span,), *self.empty[index + 1 :])
            if self.state_bytes(standalone) <= self.max_bytes and self.rank(standalone) < self.rank(best_single):
                best_single = standalone
            choices = set(beam)
            for state in beam:
                extended = self.extend(state, index, span)
                if self.state_bytes(extended) <= self.max_bytes:
                    choices.add(extended)
            beam = [
                self.empty,
                *sorted((state for state in choices if state != self.empty), key=self.rank)[: _BEAM_WIDTH - 1],
            ]
        return min((*beam, best_single), key=self.rank)

    def omissions(
        self, selected: State, pool: Sequence[tuple[tuple[float, float, int, int, int, int], int, Span]]
    ) -> PreparedContextOmissions:
        score = self.state_score(selected)
        remaining: dict[int, list[bool]] = {}
        for _, index, span in pool:
            extended = self.extend(selected, index, span)
            if self.state_score(extended) > score:
                remaining.setdefault(index, []).append(self.state_bytes(extended) > self.max_bytes)
        dropped = sum(all(pressures) for pressures in remaining.values())
        return PreparedContextOmissions(dropped_items=dropped, dropped_no_fitting_truncation=dropped)


def select_evidence(
    *, query: str, max_bytes: int, candidates: Sequence[PreparedMemoryCandidates]
) -> PreparedContextBuild:
    """Return exact original evidence with one shared selection/serialization budget."""
    entries = _entries(candidates)
    query_terms = set(analyze_fts_query(query).split())
    if not entries or not query_terms:
        return PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=())
    search = _Selection(entries, query_terms, max_bytes)
    pool = search.pool()
    selected = search.search(pool)
    omissions = search.omissions(selected, pool)
    fragments = tuple(
        PreparedContextFragment(entry.address, start, end, entry.text[start:end])
        for entry, spans in zip(entries, selected, strict=True)
        for start, end in spans
    )
    if not fragments:
        return PreparedContextBuild(
            context=PreparedContext(status="empty", content=None, content_bytes=0), origins=(), omissions=omissions
        )
    items = [entry.item(span) for entry, spans in zip(entries, selected, strict=True) for span in spans]
    content = _render(items)
    content_bytes = len(content.encode("utf-8"))
    if content_bytes != search.state_bytes(selected) or content_bytes > max_bytes:
        raise PreparedContextInvariantError("output-budget")
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
        origins=tuple(fragment.address for fragment in fragments),
        fragments=fragments,
        omissions=omissions,
    )

from __future__ import annotations

import re
from typing import Any

from .models import Chunk, ContextPack
from .store import Store

_SPEC_ID = re.compile(r"SPEC-\d+")
_ASCII = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def tokenize(task: str) -> list[str]:
    tokens = {match.group(0) for match in _SPEC_ID.finditer(task)}
    tokens.update(match.group(0).lower() for match in _ASCII.finditer(task))
    return sorted(tokens)


def build_context(store: Store, task: str, top_k: int, state: dict[str, Any]) -> ContextPack:
    tokens = tokenize(task)
    query = " ".join(tokens) or task
    candidates: dict[str, dict[str, tuple[Chunk, float]]] = {"spec": {}, "code": {}, "test": {}}

    for kind in candidates:
        for chunk, score in store.search(kind, query, max(top_k * 3, 10)):
            candidates[kind][chunk.id] = (chunk, score)

    for token in tokens:
        for kind, field in (("spec", "spec_id"), ("code", "symbol"), ("test", "test_name")):
            for chunk in store.find_exact(kind, field, token, top_k):
                previous = candidates[kind].get(chunk.id)
                score = max(10.0, previous[1] if previous else 0.0)
                candidates[kind][chunk.id] = (chunk, score)

    pack = ContextPack(task=task, state=state)
    for kind, attribute in (("spec", "specs"), ("code", "code"), ("test", "tests")):
        selected = sorted(
            candidates[kind].values(),
            key=lambda item: (-item[1], item[0].path, item[0].start_line),
        )[:top_k]
        setattr(pack, attribute, [chunk.to_dict(score) for chunk, score in selected])

    sources: list[str] = []
    for section in (pack.specs, pack.code, pack.tests):
        for item in section:
            anchor = item.get("spec_id") or item.get("symbol") or item.get("test_name") or f"L{item['start_line']}"
            source = f"{item['path']}#{anchor}"
            if source not in sources:
                sources.append(source)
    pack.sources = sources
    return pack

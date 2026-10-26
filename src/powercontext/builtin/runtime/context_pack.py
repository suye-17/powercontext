"""Portable Context Pack and prefix-delta serialization."""

# ruff: noqa: TRY003

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ContextPackError(ValueError):
    """A Context Pack, delta, or UTF-8 chunk violates the wire contract."""


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_json(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise ContextPackError("wire value must be bytes")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContextPackError("duplicate JSON field")
            result[key] = value
        return result

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(ContextPackError(f"invalid JSON constant: {value}")),
        )
    except ContextPackError:
        raise
    except (ValueError, RecursionError) as error:
        raise ContextPackError("invalid JSON") from error
    if not isinstance(value, dict):
        raise ContextPackError("wire value must be a JSON object")
    return value


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: Any) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("items must be an array")
    decoded: list[bytes] = []
    for item in value:
        if not isinstance(item, str):
            raise ContextPackError("encoded item must be a string")
        try:
            raw = base64.b64decode(item, validate=True)
            raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError) as error:
            raise ContextPackError("item must be valid Base64 containing UTF-8") from error
        decoded.append(raw)
    return tuple(decoded)


def _validate_digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ContextPackError(f"{field_name} must be a SHA-256 digest")
    return value


def _validate_items(items: Any) -> tuple[bytes, ...]:
    if isinstance(items, (bytes, bytearray, str)):
        raise ContextPackError("items must be a sequence of bytes")
    try:
        normalized = tuple(items)
    except TypeError as error:
        raise ContextPackError("items must be a sequence of bytes") from error
    for item in normalized:
        if not isinstance(item, bytes):
            raise ContextPackError("each item must be bytes")
        try:
            item.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextPackError("each item must contain valid UTF-8") from error
    return normalized


def _pack_digest(items: tuple[bytes, ...]) -> str:
    content = _canonical_json({"schema": _PACK_SCHEMA, "items": _encode_items(items)})
    return "sha256:" + hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, init=False)
class ContextPack:
    """An immutable complete pack of ordered UTF-8 byte chunks."""

    items: tuple[bytes, ...]
    digest: str

    def __init__(self, items: tuple[bytes, ...]) -> None:
        normalized = _validate_items(items)
        object.__setattr__(self, "items", normalized)
        object.__setattr__(self, "digest", _pack_digest(normalized))

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build a complete pack from UTF-8 byte chunks."""
        return cls(items)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        """Split text deterministically into UTF-8 chunks within ``chunk_bytes``."""
        if not isinstance(content, str):
            raise ContextPackError("content must be text")
        if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")

        chunks: list[bytes] = []
        current = bytearray()
        try:
            for character in content:
                encoded = character.encode("utf-8")
                if len(encoded) > chunk_bytes:
                    raise ContextPackError("chunk_bytes is too small for a UTF-8 character")
                if current and len(current) + len(encoded) > chunk_bytes:
                    chunks.append(bytes(current))
                    current.clear()
                current.extend(encoded)
        except UnicodeEncodeError as error:
            raise ContextPackError("content must be valid UTF-8 text") from error
        if current:
            chunks.append(bytes(current))
        return cls.build(tuple(chunks))

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this pack in the canonical wire format."""
        payload = {
            "schema": _PACK_SCHEMA,
            "items": _encode_items(self.items),
            "digest": self.digest,
            "byte_count": 0,
        }
        encoded = _canonical_json(payload)
        while True:
            payload["byte_count"] = len(encoded)
            updated = _canonical_json(payload)
            if len(updated) == payload["byte_count"]:
                return updated
            encoded = updated

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        """Decode a complete pack and validate its exact wire representation."""
        payload = _decode_json(data)
        if set(payload) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("pack fields do not match the schema")
        if payload["schema"] != _PACK_SCHEMA:
            raise ContextPackError("unsupported pack schema")
        if _canonical_json(payload) != data:
            raise ContextPackError("pack JSON is not canonical")
        if isinstance(payload["byte_count"], bool) or not isinstance(payload["byte_count"], int):
            raise ContextPackError("byte_count must be an integer")
        items = _decode_items(payload["items"])
        digest = _validate_digest(payload["digest"], "digest")
        pack = cls.build(items)
        if digest != pack.digest:
            raise ContextPackError("pack digest mismatch")
        if payload["byte_count"] != len(data):
            raise ContextPackError("pack byte_count mismatch")
        return pack


@dataclass(frozen=True)
class ContextPackDelta:
    """A portable prefix-preserving delta from one pack to another."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _validate_digest(self.base_digest, "base_digest")
        _validate_digest(self.target_digest, "target_digest")
        if isinstance(self.prefix_items, bool) or not isinstance(self.prefix_items, int) or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        object.__setattr__(self, "appended", _validate_items(self.appended))

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Build a delta using the longest identical prefix of chunks."""
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("base and target must be ContextPack values")
        prefix = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this delta in the canonical wire format."""
        return _canonical_json({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        """Decode a delta, accepting JSON whitespace and key-order variations."""
        payload = _decode_json(data)
        if set(payload) != {"schema", "base_digest", "target_digest", "prefix_items", "appended"}:
            raise ContextPackError("delta fields do not match the schema")
        if payload["schema"] != _DELTA_SCHEMA:
            raise ContextPackError("unsupported delta schema")
        if isinstance(payload["prefix_items"], bool) or not isinstance(payload["prefix_items"], int):
            raise ContextPackError("prefix_items must be an integer")
        delta = cls(
            base_digest=payload["base_digest"],
            target_digest=payload["target_digest"],
            prefix_items=payload["prefix_items"],
            appended=_decode_items(payload["appended"]),
        )
        return delta


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Validate and apply ``delta`` to a serialized base pack."""
    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("base and delta must use Context Pack types")
    if base.digest != delta.base_digest:
        raise ContextPackError("base digest mismatch")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("prefix_items exceeds base item count")
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("target digest mismatch")
    return target

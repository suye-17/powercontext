from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import pytest

import powercontext.builtin.runtime as public_runtime
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    PrepareContextRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class _FixedMemoryPipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_pack_wire_format_uses_canonical_json_digest_and_byte_count() -> None:
    pack = ContextPack.build((b"hello", "世界".encode()))
    canonical_digest_input = b'{"items":["aGVsbG8=","5LiW55WM"],"schema":"powercontext.context-pack.v1"}'
    expected_digest = "sha256:" + hashlib.sha256(canonical_digest_input).hexdigest()

    wire = pack.to_bytes()
    payload = json.loads(wire)

    assert payload == {
        "byte_count": len(wire),
        "digest": expected_digest,
        "items": ["aGVsbG8=", "5LiW55WM"],
        "schema": "powercontext.context-pack.v1",
    }
    assert wire == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert pack.items == (b"hello", "世界".encode())
    assert pack.digest == expected_digest
    assert pack.byte_count == len(wire)
    assert ContextPack.from_bytes(wire) == pack


def test_runtime_package_exports_context_pack_contract() -> None:
    assert public_runtime.ContextPack is ContextPack
    assert public_runtime.ContextPackDelta is ContextPackDelta
    assert public_runtime.ContextPackError is ContextPackError
    assert public_runtime.apply_context_pack_delta is apply_context_pack_delta


def test_from_content_chunks_deterministically_without_splitting_utf8() -> None:
    content = "ab世界🙂cd"

    pack = ContextPack.from_content(content, chunk_bytes=5)

    assert all(0 < len(item) <= 5 for item in pack.items)
    assert all(item.decode("utf-8").encode("utf-8") == item for item in pack.items)
    assert b"".join(pack.items).decode("utf-8") == content
    assert ContextPack.from_content(content, chunk_bytes=5) == pack


def test_from_content_rejects_limit_too_small_for_next_utf8_character() -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content("中", chunk_bytes=2)


def test_from_content_uses_default_512_byte_limit() -> None:
    pack = ContextPack.from_content("a" * 513)

    assert tuple(map(len, pack.items)) == (512, 1)
    assert b"".join(pack.items) == b"a" * 513


def test_empty_content_builds_empty_pack() -> None:
    pack = ContextPack.from_content("")

    assert pack.items == ()
    assert b"".join(pack.items) == b""
    assert ContextPack.from_bytes(pack.to_bytes()) == pack


def test_build_rejects_non_utf8_item() -> None:
    with pytest.raises(ContextPackError):
        ContextPack.build((b"\xff",))


def test_pack_byte_count_remains_exact_across_decimal_digit_boundary() -> None:
    packs = [ContextPack.build((b"a" * size,)) for size in range(500, 760)]
    lengths = {pack.byte_count for pack in packs}

    assert any(length < 1000 for length in lengths)
    assert any(length >= 1000 for length in lengths)
    assert all(pack.byte_count == len(pack.to_bytes()) for pack in packs)


def test_pack_decoder_rejects_forged_byte_count() -> None:
    payload = json.loads(ContextPack.build((b"body",)).to_bytes())
    payload["byte_count"] += 1

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def test_pack_decoder_rejects_forged_digest_with_valid_byte_count() -> None:
    payload = json.loads(ContextPack.build((b"body",)).to_bytes())
    payload["digest"] = "sha256:" + "0" * 64
    wire = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert payload["byte_count"] == len(wire)

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(wire)


@pytest.mark.parametrize(
    "wire",
    [
        b"{",
        b'{"schema":"powercontext.context-pack.v1","items":[],"digest":"sha256:bad","byte_count":1,"extra":0}',
        b'{"schema":"powercontext.context-pack.v1","items":[],"digest":"sha256:bad"}',
        b'{"schema":"wrong","items":[],"digest":"sha256:bad","byte_count":1}',
        b'{"schema":"powercontext.context-pack.v1","items":["%%%="],"digest":"sha256:bad","byte_count":1}',
    ],
)
def test_pack_decoder_rejects_invalid_wire(wire: bytes) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(wire)


def test_delta_wire_format_and_apply_rebuilds_target_from_serialized_values() -> None:
    base = ContextPack.build((b"heading\n", b"old body\n", b"old citation"))
    target = ContextPack.build((b"heading\n", b"new body\n", b"new citation"))

    delta = ContextPackDelta.between(base, target)
    delta_wire = delta.to_bytes()
    decoded_base = ContextPack.from_bytes(base.to_bytes())
    decoded_delta = ContextPackDelta.from_bytes(delta_wire)
    payload = json.loads(delta_wire)

    assert payload == {
        "appended": [
            base64.b64encode(b"new body\n").decode("ascii"),
            base64.b64encode(b"new citation").decode("ascii"),
        ],
        "base_digest": base.digest,
        "prefix_items": 1,
        "schema": "powercontext.context-pack-delta.v1",
        "target_digest": target.digest,
    }
    assert delta.prefix_items == 1
    assert delta.appended == (b"new body\n", b"new citation")
    assert delta.byte_count == len(delta_wire)
    rebuilt = apply_context_pack_delta(decoded_base, decoded_delta)
    assert rebuilt == ContextPack.from_bytes(target.to_bytes())
    assert b"".join(rebuilt.items) == b"heading\nnew body\nnew citation"


@pytest.mark.parametrize(
    ("target_items", "expected_prefix"),
    [
        ((b"changed", b"middle", b"tail"), 0),
        ((b"head", b"changed", b"tail"), 1),
        ((b"head", b"middle", b"changed"), 2),
    ],
)
def test_delta_rebuilds_first_middle_and_last_chunk_changes(
    target_items: tuple[bytes, ...], expected_prefix: int
) -> None:
    base = ContextPack.build((b"head", b"middle", b"tail"))
    target = ContextPack.build(target_items)

    delta = ContextPackDelta.between(base, target)

    assert delta.prefix_items == expected_prefix
    rebuilt = apply_context_pack_delta(
        ContextPack.from_bytes(base.to_bytes()), ContextPackDelta.from_bytes(delta.to_bytes())
    )
    assert rebuilt == target


def test_delta_accepts_any_valid_public_prefix_including_zero() -> None:
    base = ContextPack.build((b"one", b"two", b"three"))
    target = ContextPack.build((b"one", b"two", b"changed"))
    zero_prefix = ContextPackDelta(
        base_digest=base.digest,
        target_digest=target.digest,
        prefix_items=0,
        appended=target.items,
    )
    shorter_shared_prefix = ContextPackDelta(
        base_digest=base.digest,
        target_digest=target.digest,
        prefix_items=1,
        appended=(b"two", b"changed"),
    )

    assert apply_context_pack_delta(base, zero_prefix) == target
    assert apply_context_pack_delta(base, shorter_shared_prefix) == target


def test_delta_rejects_wrong_base_digest_and_out_of_range_prefix() -> None:
    base = ContextPack.build((b"base",))
    target = ContextPack.build((b"target",))
    wrong_base = ContextPackDelta(
        base_digest=target.digest,
        target_digest=target.digest,
        prefix_items=0,
        appended=target.items,
    )
    too_many_prefix = ContextPackDelta(
        base_digest=base.digest,
        target_digest=target.digest,
        prefix_items=2,
        appended=(),
    )

    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, wrong_base)
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, too_many_prefix)


@pytest.mark.parametrize(
    "wire",
    [
        b"{",
        b'{"schema":"powercontext.context-pack-delta.v1","base_digest":"sha256:a","target_digest":"sha256:b","prefix_items":0,"appended":[],"extra":0}',
        b'{"schema":"powercontext.context-pack-delta.v1","base_digest":"sha256:a","target_digest":"sha256:b","appended":[]}',
        b'{"schema":"wrong","base_digest":"sha256:a","target_digest":"sha256:b","prefix_items":0,"appended":[]}',
        b'{"schema":"powercontext.context-pack-delta.v1","base_digest":"sha256:a","target_digest":"sha256:b","prefix_items":true,"appended":[]}',
        b'{"schema":"powercontext.context-pack-delta.v1","base_digest":"sha256:a","target_digest":"sha256:b","prefix_items":-1,"appended":[]}',
        b'{"schema":"powercontext.context-pack-delta.v1","base_digest":"sha256:a","target_digest":"sha256:b","prefix_items":1.0,"appended":[]}',
        b'{"schema":"powercontext.context-pack-delta.v1","base_digest":"sha256:a","target_digest":"sha256:b","prefix_items":0,"appended":["%%%="]}',
    ],
)
def test_delta_decoder_rejects_invalid_wire(wire: bytes) -> None:
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(wire)


def test_delta_apply_rejects_target_digest_mismatch() -> None:
    base = ContextPack.build((b"base",))
    delta = ContextPackDelta(
        base_digest=base.digest,
        target_digest=ContextPack.build((b"different",)).digest,
        prefix_items=0,
        appended=(b"target",),
    )

    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, delta)


def test_runtime_incremental_prepare_rebuilds_after_memory_append_and_query_switch(tmp_path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'context-pack.db'}")),
            candidate_pipeline=_FixedMemoryPipeline(),
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Context pack", summary="Incremental context", idempotency_key="context-pack")
            )
            source = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            context = runtime.context.for_scope(scope.scope_id)

            first_capture = await source.capture(
                CaptureSource(
                    source_id="turn-1",
                    content="Deployment uses blue green rollout and preserves the service endpoint.",
                    metadata={},
                )
            )
            await memory.flush()
            first_request = PrepareContextRequest(query="blue green rollout service endpoint")
            first_pack = await context.prepare_incremental(first_request)
            first_wire = first_pack.to_bytes()
            first_plain = await context.prepare(first_request)
            assert first_capture.sequence == 1
            assert first_plain.content is not None
            assert b"".join(ContextPack.from_bytes(first_wire).items).decode("utf-8") == first_plain.content
            assert "memory_ref" in first_plain.content

            second_capture = await source.capture(
                CaptureSource(
                    source_id="turn-2",
                    content="Lease renewal runs every thirty seconds and preserves client sessions.",
                    metadata={},
                )
            )
            await memory.flush()
            second_request = PrepareContextRequest(query="lease renewal client sessions")
            second_delta = await context.prepare_incremental(
                second_request, base_pack=ContextPack.from_bytes(first_wire)
            )
            second_wire = second_delta.to_bytes()
            decoded_base = ContextPack.from_bytes(first_wire)
            decoded_delta = ContextPackDelta.from_bytes(second_wire)
            rebuilt = apply_context_pack_delta(decoded_base, decoded_delta)
            second_plain = await context.prepare(second_request)

            assert second_capture.sequence == 2
            assert isinstance(second_delta, ContextPackDelta)
            assert second_plain.content is not None
            assert b"".join(rebuilt.items).decode("utf-8") == second_plain.content
            assert rebuilt.digest == ContextPack.from_content(second_plain.content).digest
            assert "Lease renewal" in second_plain.content
            assert "entry_id" in second_plain.content

    asyncio.run(scenario())


def test_runtime_incremental_prepare_returns_empty_pack_for_no_context(tmp_path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'empty-context-pack.db'}")),
            candidate_pipeline=_FixedMemoryPipeline(),
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Empty context", summary="Empty", idempotency_key="empty-context")
            )
            prepared = await runtime.context.for_scope(scope.scope_id).prepare(
                PrepareContextRequest(query="no context exists")
            )
            pack = await runtime.context.for_scope(scope.scope_id).prepare_incremental(
                PrepareContextRequest(query="no context exists")
            )
            delta = await runtime.context.for_scope(scope.scope_id).prepare_incremental(
                PrepareContextRequest(query="no context exists"),
                base_pack=ContextPack.from_content("previous content"),
            )

            assert prepared.content is None
            assert isinstance(pack, ContextPack)
            assert pack.items == ()
            assert b"".join(ContextPack.from_bytes(pack.to_bytes()).items) == b""
            assert isinstance(delta, ContextPackDelta)
            rebuilt = apply_context_pack_delta(
                ContextPack.from_content("previous content"),
                ContextPackDelta.from_bytes(delta.to_bytes()),
            )
            assert rebuilt.items == ()
            assert b"".join(rebuilt.items) == b""

    asyncio.run(scenario())

"""T2 契约冒烟：对照 openapi v1.2.0 复验 scopes/remember/entries-list/prepare 与边界。

用法：python3 .runtime/t2/contract-smoke.py
输出：.runtime/t2/*.json（每步一条请求/响应记录，含 X-PowerContext-Request-ID）
"""

from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path(__file__).parent


def call(name: str, path: str, payload: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(  # noqa: S310 - local fixed address
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            status, body, headers = resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        status, body, headers = exc.code, exc.read().decode(), dict(exc.headers)
    record = {
        "step": name,
        "method": "POST",
        "path": path,
        "payload": payload,
        "status": status,
        "request_id": next((v for k, v in headers.items() if k.lower() == "x-powercontext-request-id"), None),
        "body": body,
    }
    (OUT / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{status}] {name}  request_id={record['request_id']}")
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def main() -> None:
    world = {
        "title": "记忆伪造师·总账本",
        "summary": "所有访客的归档总账",
        "idempotency_key": "memory-forgery:world",
    }
    _, w1 = call("01-scope-world-create", "/v1/scopes", world)
    _, w2 = call("02-scope-world-idempotent", "/v1/scopes", world)
    s3, _ = call("03-scope-world-conflict", "/v1/scopes", {**world, "title": "改了标题"})

    player = {
        "title": "记忆伪造师·玩家账本：阿澈",
        "summary": "代号 阿澈 的人生归档账本",
        "idempotency_key": "memory-forgery:player:阿澈",
    }
    _, p = call("04-scope-player-create", "/v1/scopes", player)
    scope_id = p["scope_id"] if isinstance(p, dict) else ""

    call(
        "05-remember-claim1",
        "/v1/memory/remember",
        {
            "scope_id": scope_id,
            "kind": "claim",
            "text": "玩家自述：我是你失散多年的师兄，十年前在东京学剑。",
            "reason": "玩家第一句",
        },
    )
    call(
        "06-remember-claim2",
        "/v1/memory/remember",
        {
            "scope_id": scope_id,
            "kind": "claim",
            "text": "玩家自述：我从没离开过这座城。",
            "reason": "玩家第二句",
        },
    )
    _, listing = call("07-entries-list-player", "/v1/memory/entries/list", {"scope_id": scope_id})
    _, prepared = call(
        "08-prepare-player",
        "/v1/context/prepare",
        {"scope_id": scope_id, "query": "我是你失散多年的师兄", "max_bytes": 4000},
    )
    s9, _ = call(
        "09-remember-text-too-long",
        "/v1/memory/remember",
        {"scope_id": scope_id, "kind": "claim", "text": "长" * 9000, "reason": "边界"},
    )
    s10, _ = call(
        "10-remember-kind-too-long",
        "/v1/memory/remember",
        {"scope_id": scope_id, "kind": "k" * 200, "text": "x", "reason": "边界"},
    )

    entries = listing.get("entries", []) if isinstance(listing, dict) else []
    kinds = sorted({e.get("kind") for e in entries})
    content = prepared.get("content") or "" if isinstance(prepared, dict) else ""
    checks = {
        "scope idempotent same id": isinstance(w1, dict) and w1.get("scope_id") == (w2.get("scope_id") if isinstance(w2, dict) else None),
        "scope conflict 409": s3 == 409,
        "entries contain claims": {"claim"} <= set(kinds),
        "entries no pagination field": isinstance(listing, dict) and "next_cursor" not in listing,
        "prepare ready with marker": isinstance(prepared, dict)
        and prepared.get("status") == "ready"
        and "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in content,
        "prepare recalled claim1": "师兄" in content,
        "text too long 422": s9 == 422,
        "kind too long 422": s10 == 422,
    }
    print("\nchecks:")
    for key, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {key}")
    (OUT / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
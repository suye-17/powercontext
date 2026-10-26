from __future__ import annotations

import json
import urllib.error
import urllib.request

from sdd_context.config import PROJECT_ROOT, load_settings


def main() -> int:
    settings = load_settings()
    if not settings.minimax_api_key:
        raise SystemExit("MINIMAX_API_KEY is not set")
    if not settings.minimax_model:
        raise SystemExit("MINIMAX_MODEL is not set")

    messages = [{"role": "user", "content": "Reply with exactly: ok"}]
    payload = {"model": settings.minimax_model, "messages": messages, "max_tokens": 16}
    request = urllib.request.Request(
        settings.minimax_base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.minimax_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"HTTP {error.code}: {detail}") from error

    for choice in body.get("choices", []):
        message = choice.get("message")
        if isinstance(message, dict):
            message.pop("reasoning_content", None)

    out_dir = PROJECT_ROOT / "evidence" / "minimax"
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "status": "ok",
        "url": settings.minimax_base_url + "/chat/completions",
        "model": body.get("model") or settings.minimax_model,
        "content": (body.get("choices") or [{}])[0].get("message", {}).get("content"),
        "usage": body.get("usage"),
    }
    (out_dir / "connectivity.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "ok", "model": record["model"], "content": record["content"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

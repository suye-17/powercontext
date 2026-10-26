from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from sdd_context.config import load_settings


def _strip_reasoning(body: dict) -> dict:
    for choice in body.get("choices", []):
        message = choice.get("message")
        if isinstance(message, dict):
            message.pop("reasoning_content", None)
    return body


def _build_messages(context: dict) -> list[dict]:
    specs = "\n\n".join(
        f"{item['path']}#{item.get('spec_id', '')}\n{item['text']}" for item in context.get("specs", [])
    )
    code = "\n\n".join(
        f"{item['path']}#{item.get('symbol', '')}\n{item['text']}" for item in context.get("code", [])
    )
    tests = "\n\n".join(
        f"{item['path']}#{item.get('test_name', '')}\n{item['text']}" for item in context.get("tests", [])
    )
    prompt = f"""You are modifying the Python project tasklite.
Task:
{context['task']}

Applicable specs:
{specs}

Current code:
{code}

Target tests (do not modify):
{tests}

Modify tasklite/service.py so the target tests pass and the specs are satisfied.
Return ONLY a unified diff in git format. Use paths a/tasklite/service.py and b/tasklite/service.py.
Do not modify tests or specs. Do not include markdown fences or explanations.
"""
    return [
        {
            "role": "system",
            "content": "You are a precise Python coding assistant. Output only a unified diff.",
        },
        {"role": "user", "content": prompt},
    ]


def _extract_diff(content: str) -> str:
    candidates = re.findall(r"```(?:diff)?\s*(.*?)```", content, flags=re.S)
    if not candidates:
        candidates = [content]
    for candidate in candidates:
        candidate = candidate.strip()
        if "diff --git" in candidate or candidate.startswith("---"):
            return candidate + "\n"
    return ""


def _apply_patch(after: Path, patch_path: Path) -> tuple[bool, str]:
    log_lines: list[str] = []
    for strip in (1, 0):
        check = subprocess.run(
            ["patch", f"-p{strip}", "--dry-run", "-i", str(patch_path)],
            cwd=after,
            capture_output=True,
            text=True,
        )
        log_lines.append(f"dry-run -p{strip}: exit={check.returncode}\n{check.stdout}{check.stderr}")
        if check.returncode == 0:
            applied = subprocess.run(
                ["patch", f"-p{strip}", "-i", str(patch_path)],
                cwd=after,
                capture_output=True,
                text=True,
            )
            log_lines.append(f"apply -p{strip}: exit={applied.returncode}\n{applied.stdout}{applied.stderr}")
            return applied.returncode == 0, "\n".join(log_lines)
    return False, "\n".join(log_lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="minimax-edit")
    parser.add_argument("--context", default="evidence/context/context-after-restart.json")
    parser.add_argument("--before", default="snapshots/before/tasklite")
    parser.add_argument("--after", default="snapshots/after/tasklite")
    parser.add_argument("--out", default="evidence/minimax")
    parser.add_argument("--env-file")
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    if not settings.minimax_api_key or not settings.minimax_model:
        raise SystemExit("MINIMAX_API_KEY and MINIMAX_MODEL are required")

    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    messages = _build_messages(context)
    payload = {
        "model": settings.minimax_model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0,
    }
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
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"HTTP {error.code}: {detail}") from error

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    request_record = {
        "url": settings.minimax_base_url + "/chat/completions",
        "model": settings.minimax_model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0,
    }
    (out_dir / "request.json").write_text(
        json.dumps(request_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    body = _strip_reasoning(body)
    (out_dir / "response.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    (out_dir / "output.txt").write_text(content, encoding="utf-8")

    diff = _extract_diff(content)
    if not diff:
        (out_dir / "attempt-1.txt").write_text(content, encoding="utf-8")
        print("MiniMax did not return a unified diff; see output.txt", file=sys.stderr)
        return 1
    if "diff --git a/tests/" in diff or "+++ b/tests/" in diff:
        (out_dir / "attempt-1.txt").write_text(diff, encoding="utf-8")
        print("MiniMax modified tests; refusing to apply", file=sys.stderr)
        return 1

    patch_path = out_dir / "picked.patch"
    patch_path.write_text(diff, encoding="utf-8")

    before = Path(args.before)
    after = Path(args.after)
    if after.exists():
        shutil.rmtree(after)
    shutil.copytree(before, after)
    ok, log = _apply_patch(after, patch_path.resolve())
    (out_dir / "apply.log").write_text(log + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "patch": str(patch_path), "after": str(after)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

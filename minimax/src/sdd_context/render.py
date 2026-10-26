from __future__ import annotations

import json
from typing import Any

from .models import ContextPack


def to_json(pack: ContextPack) -> str:
    return json.dumps(pack.to_dict(), ensure_ascii=False, indent=2)


def _render_section(title: str, items: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        lines.append("_No matching sources._")
        lines.append("")
        return lines
    for item in items:
        anchor = item.get("spec_id") or item.get("symbol") or item.get("test_name") or f"L{item['start_line']}"
        lines.append(f"### {item['path']}#{anchor}")
        lines.append(f"- lines: {item['start_line']}-{item['end_line']}")
        lines.append(f"- score: {item['score']}")
        lines.append("")
        lines.append("```")
        lines.append(item["text"].rstrip())
        lines.append("```")
        lines.append("")
    return lines


def to_markdown(pack: ContextPack) -> str:
    payload = pack.to_dict()
    lines = [f"# Context for: {payload['task']}", ""]
    lines.extend(_render_section("Specs", payload["specs"]))
    lines.extend(_render_section("Code", payload["code"]))
    lines.extend(_render_section("Tests", payload["tests"]))
    lines.append("## Task state")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(payload["state"], ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    for source in payload["sources"]:
        lines.append(f"- {source}")
    lines.append("")
    return "\n".join(lines)

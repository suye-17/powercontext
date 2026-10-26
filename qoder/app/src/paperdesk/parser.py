from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import VALID_STATUSES, Chunk, Paper, ParsedPaper

_REQUIRED = ("paper_id", "title", "year", "tags", "status")
_PAPER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SECTION = re.compile(r"^##\s+(.+?)\s*$")


class PaperFormatError(ValueError):
    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path


def parse_paper(path: Path, *, root: Path | None = None) -> ParsedPaper:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PaperFormatError(path, "file is not valid UTF-8") from exc
    lines = raw.splitlines()
    meta, body_start = _parse_front_matter(lines, path)
    meta = _validate_metadata(meta, path)
    tags = _normalize_tags(meta["tags"], path)
    paper_id = meta["paper_id"]
    display_path = path.relative_to(root).as_posix() if root else path.as_posix()
    body = "\n".join(lines[body_start:])
    document_id = _digest(paper_id)[:32]
    title = meta["title"]
    chunks = _build_chunks(
        lines=lines,
        body_start=body_start,
        paper_id=paper_id,
        title=title,
        tags=tags,
    )
    paper = Paper(
        paper_id=paper_id,
        title=title,
        authors=meta.get("authors", ""),
        year=int(meta["year"]),
        tags=tags,
        status=meta["status"],
        path=display_path,
        content=body,
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
    )
    return ParsedPaper(paper=paper, chunks=chunks)


def parse_library(root: Path) -> list[ParsedPaper]:
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"library root is not a directory: {root}")
    parsed = [parse_paper(path, root=root) for path in sorted(root.rglob("*.md"))]
    seen: dict[str, str] = {}
    for item in parsed:
        previous = seen.get(item.paper.paper_id)
        if previous is not None:
            raise PaperFormatError(
                Path(item.paper.path),
                f"duplicate paper_id {item.paper.paper_id!r}; already defined in {previous}",
            )
        seen[item.paper.paper_id] = item.paper.path
    return sorted(parsed, key=lambda item: item.paper.paper_id)


def _parse_front_matter(lines: list[str], path: Path) -> tuple[dict[str, str], int]:
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or lines[index].strip() != "---":
        raise PaperFormatError(path, "missing front matter; the file must start with ---")
    start = index + 1
    end = start
    while end < len(lines) and lines[end].strip() != "---":
        end += 1
    if end >= len(lines):
        raise PaperFormatError(path, "unterminated front matter; expected a closing ---")
    meta: dict[str, str] = {}
    for offset in range(start, end):
        line = lines[offset].strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise PaperFormatError(path, f"invalid front matter line {offset + 1}: {line!r}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        meta[key] = value
    return meta, end + 1


def _validate_metadata(meta: dict[str, str], path: Path) -> dict[str, str]:
    missing = [key for key in _REQUIRED if not meta.get(key)]
    if missing:
        raise PaperFormatError(path, "missing required front matter keys: " + ", ".join(missing))
    if not _PAPER_ID.match(meta["paper_id"]):
        raise PaperFormatError(
            path,
            f"invalid paper_id {meta['paper_id']!r}; use lowercase letters, digits, and hyphens",
        )
    try:
        year = int(meta["year"])
    except ValueError as exc:
        raise PaperFormatError(path, f"year must be an integer, got {meta['year']!r}") from exc
    if not 1900 <= year <= 2100:
        raise PaperFormatError(path, f"year out of range: {year}")
    if meta["status"] not in VALID_STATUSES:
        raise PaperFormatError(
            path,
            f"invalid status {meta['status']!r}; expected one of {', '.join(VALID_STATUSES)}",
        )
    return meta


def _normalize_tags(value: str, path: Path) -> tuple[str, ...]:
    tags: list[str] = []
    for raw in value.split(","):
        tag = " ".join(raw.strip().lower().split())
        if tag and tag not in tags:
            tags.append(tag)
    if not tags:
        raise PaperFormatError(path, "tags must contain at least one value")
    return tuple(tags)


def _build_chunks(
    *,
    lines: list[str],
    body_start: int,
    paper_id: str,
    title: str,
    tags: tuple[str, ...],
) -> tuple[Chunk, ...]:
    headings: list[tuple[int, str]] = []
    for index in range(body_start, len(lines)):
        match = _SECTION.match(lines[index])
        if match:
            headings.append((index, match.group(1).strip()))

    if not headings:
        first = _first_non_empty(lines, body_start)
        last = _last_non_empty(lines, body_start)
        if first is None or last is None:
            return ()
        return (_make_chunk(paper_id, title, tags, "Overview", 0, first, last, lines),)

    chunks: list[Chunk] = []
    for order, (heading_index, section) in enumerate(headings):
        next_heading = headings[order + 1][0] if order + 1 < len(headings) else len(lines)
        last = _last_non_empty(lines, heading_index + 1, next_heading)
        if last is None:
            continue
        chunks.append(
            _make_chunk(
                paper_id,
                title,
                tags,
                section,
                len(chunks),
                heading_index,
                last,
                lines,
            )
        )
    return tuple(chunks)


def _make_chunk(
    paper_id: str,
    title: str,
    tags: tuple[str, ...],
    section: str,
    order: int,
    start: int,
    end: int,
    lines: list[str],
) -> Chunk:
    text = "\n".join(lines[start : end + 1]).strip()
    searchable = " ".join((title, *tags, section, text)).lower()
    chunk_id = _digest(f"{paper_id}\n{order}\n{section}\n{text}")[:32]
    return Chunk(
        id=chunk_id,
        document_id=_digest(paper_id)[:32],
        paper_id=paper_id,
        section=section,
        order_index=order,
        start_line=start + 1,
        end_line=end + 1,
        text=text,
        searchable_text=searchable,
    )


def _first_non_empty(lines: list[str], start: int, end: int | None = None) -> int | None:
    limit = len(lines) if end is None else end
    for index in range(start, limit):
        if lines[index].strip():
            return index
    return None


def _last_non_empty(lines: list[str], start: int, end: int | None = None) -> int | None:
    limit = len(lines) if end is None else end
    for index in range(limit - 1, start - 1, -1):
        if lines[index].strip():
            return index
    return None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

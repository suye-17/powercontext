from __future__ import annotations

import ast
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Chunk, Document

_SPEC_HEADING = re.compile(r"^##\s+(SPEC-\d+)\s+(.*)$")
_EXCLUDED_DIRS = {".venv", "__pycache__", ".pytest_cache", ".runtime", ".git", "build", "dist"}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _document(root: Path, path: Path, kind: str, title: str, content: str) -> Document:
    relative = _relative(root, path)
    return Document(
        id=_sha256(relative),
        path=relative,
        kind=kind,
        title=title,
        content=content,
        content_hash=_sha256(content),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _chunk_id(relative: str, kind: str, anchor: str, start: int, end: int) -> str:
    return _sha256(f"{relative}:{kind}:{anchor}:{start}:{end}")


def parse_specs(root: Path) -> tuple[list[Document], list[Chunk]]:
    documents: list[Document] = []
    chunks: list[Chunk] = []
    for path in sorted((root / "specs").glob("*.md")):
        content = path.read_text(encoding="utf-8")
        relative = _relative(root, path)
        document = _document(root, path, "spec", path.stem, content)
        documents.append(document)
        lines = content.splitlines()
        headings = [
            (index + 1, match)
            for index, line in enumerate(lines)
            if (match := _SPEC_HEADING.match(line)) is not None
        ]
        for position, (line_number, match) in enumerate(headings):
            spec_id, title = match.group(1), match.group(2).strip()
            end_line = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines)
            body = "\n".join(lines[line_number:end_line]).strip()
            text = f"{spec_id} {title}\n{body}".strip()
            chunks.append(
                Chunk(
                    id=_chunk_id(relative, "spec", spec_id, line_number, end_line),
                    document_id=document.id,
                    kind="spec",
                    path=relative,
                    title=title,
                    text=text,
                    searchable_text=f"{spec_id} {title} {body}",
                    start_line=line_number,
                    end_line=end_line,
                    spec_id=spec_id,
                )
            )
    return documents, chunks


def _symbol_chunks(root: Path, path: Path, kind: str) -> tuple[Document, list[Chunk]]:
    content = path.read_text(encoding="utf-8")
    relative = _relative(root, path)
    document = _document(root, path, kind, path.name, content)
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return document, []
    lines = content.splitlines()
    chunks: list[Chunk] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        symbol = node.name
        start_line = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
        end_line = node.end_lineno or node.lineno
        text = "\n".join(lines[start_line - 1 : end_line])
        chunks.append(
            Chunk(
                id=_chunk_id(relative, kind, symbol, start_line, end_line),
                document_id=document.id,
                kind=kind,
                path=relative,
                title=symbol,
                text=text,
                searchable_text=f"{relative} {symbol} {text}",
                start_line=start_line,
                end_line=end_line,
                symbol=symbol,
            )
        )
    return document, chunks


def parse_code(root: Path) -> tuple[list[Document], list[Chunk]]:
    documents: list[Document] = []
    chunks: list[Chunk] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in _EXCLUDED_DIRS for part in path.parts) or "tests" in path.parts:
            continue
        document, symbol_chunks = _symbol_chunks(root, path, "code")
        documents.append(document)
        chunks.extend(symbol_chunks)
    return documents, chunks


def parse_tests(root: Path) -> tuple[list[Document], list[Chunk]]:
    documents: list[Document] = []
    chunks: list[Chunk] = []
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return documents, chunks
    for path in sorted(tests_root.rglob("test_*.py")):
        content = path.read_text(encoding="utf-8")
        relative = _relative(root, path)
        document = _document(root, path, "test", path.name, content)
        documents.append(document)
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        lines = content.splitlines()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                continue
            test_name = node.name
            start_line = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
            end_line = node.end_lineno or node.lineno
            text = "\n".join(lines[start_line - 1 : end_line])
            chunks.append(
                Chunk(
                    id=_chunk_id(relative, "test", test_name, start_line, end_line),
                    document_id=document.id,
                    kind="test",
                    path=relative,
                    title=test_name,
                    text=text,
                    searchable_text=f"{relative} {test_name} {text}",
                    start_line=start_line,
                    end_line=end_line,
                    test_name=test_name,
                )
            )
    return documents, chunks


def ingest_project(root: Path) -> tuple[list[Document], list[Chunk]]:
    documents: list[Document] = []
    chunks: list[Chunk] = []
    for parser in (parse_specs, parse_code, parse_tests):
        parsed_documents, parsed_chunks = parser(root)
        documents.extend(parsed_documents)
        chunks.extend(parsed_chunks)
    documents.sort(key=lambda item: item.path)
    chunks.sort(key=lambda item: (item.path, item.start_line))
    return documents, chunks

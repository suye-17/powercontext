from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from paperdesk.parser import parse_library
from paperdesk.store import PaperStore

_APP_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _APP_ROOT / ".runtime" / "tests"


def runtime_dir(prefix: str) -> Path:
    path = _RUNTIME / f"{prefix}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def store() -> Iterator[PaperStore]:
    db_dir = runtime_dir("store")
    instance = PaperStore(db_dir)
    try:
        yield instance
    finally:
        instance.close()
        shutil.rmtree(db_dir, ignore_errors=True)


@pytest.fixture
def library_path() -> Iterator[Path]:
    root = runtime_dir("library")
    _write_paper(
        root / "rag-survey.md",
        paper_id="rag-survey",
        title="Retrieval Augmented Generation: A Survey",
        year=2023,
        tags="rag, retrieval, survey",
        status="done",
        sections={
            "Abstract": "Retrieval augmented generation combines a retriever with a generator. "
            "This survey maps the design space of modern systems.",
            "Method": "We organize systems by retrieval granularity and adaptation. "
            "Hybrid fusion joins sparse and dense rankings.",
            "Evaluation": "We compare answer faithfulness and citation coverage across benchmarks.",
        },
    )
    _write_paper(
        root / "rerank-notes.md",
        paper_id="rerank-notes",
        title="Reranking for Retrieval Pipelines",
        year=2024,
        tags="rerank, retrieval",
        status="reading",
        sections={
            "Abstract": "Reranking refines a first-stage candidate list before generation.",
            "Method": "A cross encoder scores query passage pairs. "
            "Hybrid fusion merges lexical and dense signals.",
            "Results": "Reranking improves nDCG and reduces irrelevant context in the answer.",
        },
    )
    _write_paper(
        root / "chunking-notes.md",
        paper_id="chunking-notes",
        title="Chunking Strategies for Long Documents",
        year=2022,
        tags="chunking, rag",
        status="unread",
        sections={
            "Abstract": "Chunking decides what a retriever can see.",
            "Method": "Fixed windows, sentence packing, and semantic boundaries "
            "trade recall against precision.",
            "Limitations": "Small chunks lose context while large chunks dilute embeddings.",
        },
    )
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _write_paper(
    path: Path,
    *,
    paper_id: str,
    title: str,
    year: int,
    tags: str,
    status: str,
    sections: dict[str, str],
) -> None:
    lines = [
        "---",
        f"paper_id: {paper_id}",
        f"title: {title}",
        "authors: PaperDesk Sample",
        f"year: {year}",
        f"tags: {tags}",
        f"status: {status}",
        "---",
        "",
    ]
    for heading, body in sections.items():
        lines.extend([f"## {heading}", body, ""])
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def library(library_path: Path):
    return parse_library(library_path)

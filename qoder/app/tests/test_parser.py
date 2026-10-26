from pathlib import Path

import pytest

from paperdesk.parser import PaperFormatError, parse_library, parse_paper

VALID = """---
paper_id: rag-survey
title: RAG Survey
authors: Self-authored notes
year: 2024
tags: RAG, retrieval, rag
status: unread
---

# RAG Survey

## Abstract
Retrieval augmented generation combines retrieval.

## Method
Hybrid retrieval and reranking.

## Results
Better answers.
"""


def write(root: Path, name: str, content: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_valid_paper(tmp_path: Path) -> None:
    path = write(tmp_path, "rag.md", VALID)

    parsed = parse_paper(path, root=tmp_path)

    assert parsed.paper.paper_id == "rag-survey"
    assert parsed.paper.title == "RAG Survey"
    assert parsed.paper.authors == "Self-authored notes"
    assert parsed.paper.year == 2024
    assert parsed.paper.tags == ("rag", "retrieval")
    assert parsed.paper.status == "unread"
    assert parsed.paper.path == "rag.md"
    assert [chunk.section for chunk in parsed.chunks] == ["Abstract", "Method", "Results"]

    abstract = parsed.chunks[0]
    assert abstract.start_line == 12
    assert abstract.end_line == 13
    assert abstract.text.startswith("## Abstract")
    assert "rag survey" in abstract.searchable_text
    assert "rag" in abstract.searchable_text
    assert abstract.id == parse_paper(path, root=tmp_path).chunks[0].id


def test_missing_front_matter(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", "# Title\n\nbody\n")

    with pytest.raises(PaperFormatError, match="missing front matter"):
        parse_paper(path)


def test_missing_required_key(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", VALID.replace("year: 2024\n", ""))

    with pytest.raises(PaperFormatError, match="year"):
        parse_paper(path)


def test_invalid_status(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", VALID.replace("status: unread", "status: archived"))

    with pytest.raises(PaperFormatError, match="invalid status"):
        parse_paper(path)


def test_invalid_paper_id(tmp_path: Path) -> None:
    path = write(tmp_path, "x.md", VALID.replace("paper_id: rag-survey", "paper_id: RAG Survey"))

    with pytest.raises(PaperFormatError, match="invalid paper_id"):
        parse_paper(path)


def test_no_sections_uses_overview(tmp_path: Path) -> None:
    content = """---
paper_id: notes
title: Notes
year: 2025
tags: notes
status: reading
---

Just a paragraph.
"""
    path = write(tmp_path, "notes.md", content)

    parsed = parse_paper(path)

    assert [chunk.section for chunk in parsed.chunks] == ["Overview"]
    assert "Just a paragraph." in parsed.chunks[0].text


def test_empty_section_is_skipped(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "x.md",
        VALID.replace("## Method\nHybrid retrieval and reranking.\n\n", "## Method\n\n"),
    )

    parsed = parse_paper(path)

    assert [chunk.section for chunk in parsed.chunks] == ["Abstract", "Results"]


def test_parse_library_sorts_and_detects_duplicates(tmp_path: Path) -> None:
    write(
        tmp_path,
        "b.md",
        VALID.replace("paper_id: rag-survey", "paper_id: b-paper").replace("title: RAG Survey", "title: B"),
    )
    write(
        tmp_path,
        "a.md",
        VALID.replace("paper_id: rag-survey", "paper_id: a-paper").replace("title: RAG Survey", "title: A"),
    )

    parsed = parse_library(tmp_path)

    assert [item.paper.paper_id for item in parsed] == ["a-paper", "b-paper"]

    write(
        tmp_path,
        "c.md",
        VALID.replace("paper_id: rag-survey", "paper_id: a-paper").replace("title: RAG Survey", "title: C"),
    )
    with pytest.raises(PaperFormatError, match="duplicate paper_id"):
        parse_library(tmp_path)

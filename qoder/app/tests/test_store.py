from __future__ import annotations

import pytest

from paperdesk.store import PaperStore, StoreError


def test_ingest_is_idempotent(store: PaperStore, library) -> None:
    first = store.replace_all(library)
    second = store.replace_all(library)

    assert first == second
    assert first["documents"] == 3
    assert first["chunks"] == len({chunk.id for parsed in library for chunk in parsed.chunks})
    assert [paper.paper_id for paper in store.list_papers()] == [
        "chunking-notes",
        "rag-survey",
        "rerank-notes",
    ]
    assert store.get_paper("rag-survey").title == "Retrieval Augmented Generation: A Survey"
    assert [chunk.section for chunk in store.get_chunks("rerank-notes")] == [
        "Abstract",
        "Method",
        "Results",
    ]


def test_search_returns_citable_section(store: PaperStore, library) -> None:
    store.replace_all(library)

    hits = store.search("cross encoder", limit=5)

    assert hits
    top = hits[0]
    assert top.paper_id == "rerank-notes"
    assert top.section == "Method"
    assert top.path == "rerank-notes.md"
    assert "cross encoder" in top.text.lower()
    assert 0 < top.start_line <= top.end_line
    assert top.score > 0
    assert top.tags == ("rerank", "retrieval")


def test_search_applies_tag_year_and_status_filters(store: PaperStore, library) -> None:
    store.replace_all(library)

    tagged = store.search("retrieval", tag="rerank", limit=10)
    assert tagged
    assert {hit.paper_id for hit in tagged} == {"rerank-notes"}

    recent = store.search("retrieval", year_min=2024, limit=10)
    assert recent
    assert {hit.paper_id for hit in recent} == {"rerank-notes"}

    unread = store.search("chunking", status="unread", limit=10)
    assert unread
    assert {hit.paper_id for hit in unread} == {"chunking-notes"}

    assert store.search("chunking", year_min=2023, limit=10) == []


def test_identifier_query_boosts_matching_paper(store: PaperStore, library) -> None:
    store.replace_all(library)

    hits = store.search("rag-survey", limit=5)

    assert hits
    assert hits[0].paper_id == "rag-survey"


def test_section_query_boosts_exact_section(store: PaperStore, library) -> None:
    store.replace_all(library)

    hits = store.search("evaluation", limit=5)

    assert hits
    assert hits[0].paper_id == "rag-survey"
    assert hits[0].section == "Evaluation"


def test_unknown_query_returns_nothing(store: PaperStore, library) -> None:
    store.replace_all(library)

    assert store.search("nonexistentterm", limit=5) == []
    assert store.search("   ", limit=5) == []


def test_limit_and_reset(store: PaperStore, library) -> None:
    store.replace_all(library)

    assert len(store.search("retrieval", limit=2)) <= 2
    store.reset()
    assert store.counts() == {"documents": 0, "chunks": 0}


def test_vector_channel_adds_candidates_and_scores(store: PaperStore, library) -> None:
    embeddings = {}
    for parsed in library:
        for chunk in parsed.chunks:
            if parsed.paper.paper_id == "rerank-notes" and chunk.section == "Method":
                embeddings[chunk.id] = [1.0, 0.0, 0.0, 0.0]
            else:
                embeddings[chunk.id] = [0.0, 1.0, 0.0, 0.0]

    counts = store.replace_all(library, embeddings=embeddings)

    assert counts["chunks"] == len(embeddings)
    assert store.embedding_dim == 4
    assert store.embedding_count() == len(embeddings)

    hits = store.search("chunking", limit=10, query_embedding=[1.0, 0.0, 0.0, 0.0])

    vector_candidates = [hit for hit in hits if hit.paper_id == "rerank-notes" and hit.section == "Method"]
    assert vector_candidates
    assert vector_candidates[0].vector_score is not None
    assert vector_candidates[0].vector_score > 0.99
    assert all(hit.fts_score >= 0 for hit in hits)


def test_embedding_dimension_mismatch_is_rejected(store: PaperStore, library) -> None:
    embeddings = {chunk.id: [0.1, 0.2, 0.3, 0.4] for parsed in library for chunk in parsed.chunks}
    store.replace_all(library, embeddings=embeddings)

    smaller = {chunk.id: [0.1, 0.2, 0.3] for parsed in library for chunk in parsed.chunks}
    with pytest.raises(StoreError, match="dimension mismatch"):
        store.replace_all(library, embeddings=smaller)

    with pytest.raises(StoreError, match="query embedding"):
        store.search("retrieval", query_embedding=[0.0] * 5)

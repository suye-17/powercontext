import asyncio
import hashlib

import pytest

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryEntryVersion, MemoryHit
from powercontext.builtin.runtime.models import (
    GetMemoryEntryRequest,
    MemoryEntryRecord,
    MemorySearchPage,
    SearchMemoryRequest,
)
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceMaterialization,
    SourceRef,
    TextEvidence,
    project_source_for_transport,
)
from powercontext.sources import pdf_pages as api
from tests.pdf_support import make_pdf


def test_parser_preserves_all_physical_pages_and_full_byte_digest() -> None:
    content = make_pdf("Revenue 1234 and operating income 567", "", "Debt 890")
    pages = api.parse_pdf_document(api.PDFDocumentInput(document_id=" Annual report ", content=content))
    assert isinstance(pages, tuple)
    assert [(page.page_number, page.text.strip()) for page in pages] == [
        (1, "Revenue 1234 and operating income 567"),
        (2, ""),
        (3, "Debt 890"),
    ]
    assert all(page.document_id == " Annual report " for page in pages)
    assert {page.document_sha256 for page in pages} == {hashlib.sha256(content).hexdigest()}


@pytest.mark.parametrize("content", [b"", b"not a PDF", b"%PDF-1.7\ntruncated"])
def test_invalid_documents_raise_value_error(content: bytes) -> None:
    with pytest.raises(ValueError):
        api.parse_pdf_document(api.PDFDocumentInput(document_id="invalid", content=content))


def test_late_unreadable_page_fails_the_whole_document() -> None:
    with pytest.raises(ValueError):
        api.parse_pdf_document(
            api.PDFDocumentInput(document_id="broken", content=make_pdf("Readable", "", corrupt_last=True))
        )


@pytest.mark.parametrize("document_id", ["", " \t\n"])
def test_document_ids_must_not_be_blank(document_id: str) -> None:
    with pytest.raises(ValueError):
        api.parse_pdf_document(api.PDFDocumentInput(document_id=document_id, content=make_pdf("Text")))


def test_empty_password_permissions_are_readable() -> None:
    pages = api.parse_pdf_document(api.PDFDocumentInput(document_id="permissions", content=make_pdf("", password="")))
    assert [(page.page_number, page.text) for page in pages] == [(1, "")]


def test_password_protected_document_is_rejected() -> None:
    with pytest.raises(ValueError):
        api.parse_pdf_document(
            api.PDFDocumentInput(document_id="locked", content=make_pdf("Private", password="secret"))  # noqa: S106
        )


def test_page_sources_keep_exact_input_projection_and_revision_identity() -> None:

    async def scenario() -> None:
        registry = SourceDefinitionRegistry((api.PDF_PAGE_SOURCE_DEFINITION,))
        assert api.PDF_PAGE_SOURCE_DEFINITION.name == "pdf-page"
        assert api.PDF_PAGE_SOURCE_DEFINITION.version == "1"
        content = make_pdf("Revenue 123", "")
        sources = []
        for document_id in ("report:1", "report/1", "财报:年度", "e\u0301", "é", "文" * 1000):
            for page in api.parse_pdf_document(api.PDFDocumentInput(document_id=document_id, content=content)):
                source = await registry.resolve(page)
                sources.append(source)
                assert source.materialization == SourceMaterialization.CAPTURED
                assert await registry.read(source) == page
                assert (await registry.resolve(page)).name == source.name
                assert len(source.name) <= 256
                projection = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
                assert projection.source_type == "pdf-page"
                assert projection.source_id == source.name
                assert projection.content == f"{page.document_id}\n{page.text}"
                assert projection.metadata == {
                    "document_id": page.document_id,
                    "document_sha256": hashlib.sha256(content).hexdigest(),
                    "page_number": page.page_number,
                }
                observation = project_source_for_transport(registry, source)
                restored = api.PDF_PAGE_SOURCE_DEFINITION.source_class.model_validate(observation.payload)
                assert await registry.read(restored) == page
        assert len({source.name for source in sources}) == len(sources)
        revised = api.parse_pdf_document(
            api.PDFDocumentInput(document_id="report:1", content=make_pdf("Revenue 123", "", title="revision"))
        )
        assert (await registry.resolve(revised[0])).name != sources[0].name
        first = await registry.read(sources[0])
        assert isinstance(first, api.PDFPageInput)
        assert first.document_sha256 == hashlib.sha256(content).hexdigest()

    asyncio.run(scenario())


def _search_entry(index: int, sources: tuple[SourceRef, ...]) -> tuple[MemoryHit, MemoryEntryRecord]:
    reference = ArtifactRef(family="memory", artifact_id="reports", revision=3)
    hit = MemoryHit(
        memory_ref=reference,
        entry_id=f"entry-{index:03}",
        entry_version_id=f"version-{index:03}",
        text=f"Page text {index}",
        score=1,
        matched_by=("fts",),
    )
    record = MemoryEntryRecord(
        memory_ref=reference,
        state="active",
        entry=MemoryEntryVersion(
            memory_artifact_id=reference.artifact_id,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
            version=1,
            previous_version_id=None,
            kind="source-evidence",
            text=hit.text,
            entry_content_hash="a" * 64,
            created_in_revision=1,
            sources=sources,
        ),
    )
    return hit, record


class _MemoryFixture:
    def __init__(self, entries: tuple[tuple[MemoryHit, MemoryEntryRecord], ...], rankings=None) -> None:
        self.entries = {record.citation.entry_id: record for _, record in entries}
        self.default_hits = tuple(hit for hit, _ in entries)
        self.rankings = rankings or {}
        self.requests: list[SearchMemoryRequest] = []
        self.reads: list[MemoryCitation] = []

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage:
        self.requests.append(request)
        return MemorySearchPage(
            memory_ref=None, mode="fts", hits=self.rankings.get(request.query, self.default_hits)[: request.limit]
        )

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord:
        self.reads.append(request.citation)
        record = self.entries[request.citation.entry_id]
        assert request.citation == record.citation
        return record


def test_search_overfetches_and_filters_before_returning_distinct_pages() -> None:
    entries = tuple(
        _search_entry(
            index,
            (
                SourceRef(
                    source_type="content" if index < 70 else "pdf-page",
                    source_id="same-page" if index < 80 else f"page-{index}",
                ),
            ),
        )
        for index in range(100)
    )
    memory = _MemoryFixture(entries)
    citations = asyncio.run(api.search_pdf_pages(memory, "Annual revenue and operating income", limit=20))
    assert len(citations) == 20
    assert {memory.entries[c.entry_id].entry.sources[0].source_type for c in citations} == {"pdf-page"}
    assert len({memory.entries[c.entry_id].entry.sources[0].source_id for c in citations}) == 20
    assert memory.requests[0].query == "Annual revenue and operating income"
    assert all(request.limit == 100 and request.mode == "fts" for request in memory.requests)
    assert len(memory.requests) <= 4
    assert len(memory.reads) <= 100


@pytest.mark.parametrize(("limit", "candidate_limit"), [(1, 20), (10, 50), (20, 100), (1000, 100)])
def test_search_candidate_and_readback_budgets(limit: int, candidate_limit: int) -> None:
    memory = _MemoryFixture(
        tuple(_search_entry(i, (SourceRef(source_type="pdf-page", source_id=f"p-{i}"),)) for i in range(120))
    )
    citations = asyncio.run(api.search_pdf_pages(memory, "debt", limit=limit))
    assert len(citations) == min(limit, candidate_limit)
    assert len(memory.requests) == 1
    assert memory.requests[0].limit == candidate_limit
    assert len(memory.reads) <= candidate_limit


def test_search_returns_short_tuple_when_candidates_are_ambiguous_or_insufficient() -> None:
    page = SourceRef(source_type="pdf-page", source_id="one")
    other = SourceRef(source_type="pdf-page", source_id="two")
    memory = _MemoryFixture(
        tuple(_search_entry(i, sources) for i, sources in enumerate(((), (page, other), (page,), (page,))))
    )
    citations = asyncio.run(api.search_pdf_pages(memory, "debt", limit=20))
    assert citations == (memory.entries["entry-002"].citation,)


def test_search_rejects_citations_mixing_a_pdf_page_with_other_evidence() -> None:
    memory = _MemoryFixture((
        _search_entry(
            0,
            (
                SourceRef(source_type="pdf-page", source_id="page"),
                SourceRef(source_type="content", source_id="note"),
            ),
        ),
    ))
    assert asyncio.run(api.search_pdf_pages(memory, "debt", limit=20)) == ()


def test_search_merges_generic_query_variants_and_preserves_exact_citations() -> None:
    entries = tuple(_search_entry(i, (SourceRef(source_type="pdf-page", source_id=f"p-{i}"),)) for i in range(3))
    first, second, third = (entry[0] for entry in entries)
    question = "alpha beta gamma delta epsilon zeta"
    memory = _MemoryFixture(
        entries,
        {
            question: (first, second),
            "alpha beta": (second,),
            "gamma delta": (third,),
            "epsilon zeta": (second,),
        },
    )
    citations = asyncio.run(api.search_pdf_pages(memory, question, limit=3))
    assert citations == tuple(memory.entries[hit.entry_id].citation for hit in (second, first, third))
    assert len(memory.reads) == 3
    assert len(memory.requests) <= 4


@pytest.mark.parametrize("limit", [0, -1])
def test_search_rejects_non_positive_limits(limit: int) -> None:
    memory = _MemoryFixture(())
    with pytest.raises(ValueError):
        asyncio.run(api.search_pdf_pages(memory, "debt", limit=limit))
    assert memory.requests == []

import asyncio
import hashlib

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryVersion, MemoryHit
from powercontext.builtin.runtime.models import (
    GetMemoryEntryRequest,
    MemoryEntriesPage,
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


def test_late_invalid_content_stream_array_fails_the_whole_document() -> None:
    with pytest.raises(ValueError):
        api.parse_pdf_document(
            api.PDFDocumentInput(
                document_id="broken-array",
                content=make_pdf("Readable first page", "Second page", corrupt_last=True, content_array=True),
            )
        )


def test_valid_content_stream_arrays_and_empty_arrays_preserve_pages() -> None:
    pages = api.parse_pdf_document(
        api.PDFDocumentInput(document_id="streams", content=make_pdf("Revenue 123", "", content_array=True))
    )
    assert [(page.page_number, page.text.strip()) for page in pages] == [(1, "Revenue 123"), (2, "")]


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


_REFERENCE = ArtifactRef(family="memory", artifact_id="reports", revision=3)


def _page_source(page_number: int, *, document_id: str = "doc", digest: str = "a" * 64) -> SourceRef:
    registry = SourceDefinitionRegistry((api.PDF_PAGE_SOURCE_DEFINITION,))
    page = api.PDFPageInput(document_id=document_id, document_sha256=digest, page_number=page_number, text="")
    return SourceRef(source_type="pdf-page", source_id=asyncio.run(registry.resolve(page)).name)


def _record(index: int, text: str, sources: tuple[SourceRef, ...]) -> MemoryEntryRecord:
    return MemoryEntryRecord(
        memory_ref=_REFERENCE,
        state="active",
        entry=MemoryEntryVersion(
            memory_artifact_id=_REFERENCE.artifact_id,
            entry_id=f"entry-{index:03}",
            entry_version_id=f"version-{index:03}",
            version=1,
            previous_version_id=None,
            kind="source-evidence",
            text=text,
            entry_content_hash="a" * 64,
            created_in_revision=1,
            sources=sources,
        ),
    )


def _page_record(
    index: int, page_number: int, body: str, *, document_id: str = "doc", digest: str = "a" * 64
) -> MemoryEntryRecord:
    return _record(
        index,
        f"{document_id}\n{body}",
        (_page_source(page_number, document_id=document_id, digest=digest),),
    )


class _ListingMemory:
    """Memory facade that exposes the scoped entry listing."""

    def __init__(self, records: tuple[MemoryEntryRecord, ...]) -> None:
        self.records = records
        self.list_calls = 0
        self.search_calls = 0

    async def list(self, /, *, include_inactive: bool = False) -> MemoryEntriesPage:
        self.list_calls += 1
        return MemoryEntriesPage(memory_ref=_REFERENCE, entries=self.records)

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage:
        self.search_calls += 1
        return MemorySearchPage(memory_ref=_REFERENCE, mode="fts", hits=())

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord:
        raise AssertionError("the listing path must not read entries one by one")  # noqa: TRY003


class _HitMemory:
    """Memory facade with search and get but no entry listing."""

    def __init__(self, records: tuple[MemoryEntryRecord, ...]) -> None:
        self.records = {record.entry.entry_id: record for record in records}
        self.requests: list[SearchMemoryRequest] = []

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage:
        self.requests.append(request)
        hits = tuple(
            MemoryHit(
                memory_ref=record.memory_ref,
                entry_id=record.entry.entry_id,
                entry_version_id=record.entry.entry_version_id,
                text=record.entry.text,
                score=1,
                matched_by=("fts",),
            )
            for record in self.records.values()
        )
        return MemorySearchPage(memory_ref=_REFERENCE, mode="fts", hits=hits[: request.limit])

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord:
        return self.records[request.citation.entry_id]


def test_search_returns_only_entries_citing_exactly_one_pdf_page() -> None:
    other = SourceRef(source_type="content", source_id="note")
    mixed = _record(0, "doc\nlegal proceedings", (_page_source(1), other))
    text_only = _record(1, "doc\nlegal proceedings", (other,))
    exact = _page_record(2, 3, "legal proceedings and litigation")
    memory = _ListingMemory((mixed, text_only, exact))
    citations = asyncio.run(api.search_pdf_pages(memory, "legal proceedings", limit=10))
    assert citations == (exact.citation,)


def test_search_returns_distinct_pages_for_entries_that_share_a_page() -> None:
    source = _page_source(7)
    memory = _ListingMemory((
        _record(0, "doc\nrevenue growth", (source,)),
        _record(1, "doc\nrevenue growth", (source,)),
    ))
    citations = asyncio.run(api.search_pdf_pages(memory, "revenue growth", limit=10))
    assert len(citations) == 1


def test_search_ranks_matching_pages_above_unrelated_pages() -> None:
    unrelated = _page_record(0, 1, "annual report cover page")
    matching = _page_record(1, 2, "operating income and depreciation")
    memory = _ListingMemory((unrelated, matching))
    citations = asyncio.run(api.search_pdf_pages(memory, "operating income", limit=10))
    assert citations == (matching.citation,)


def test_search_weights_the_page_heading_field() -> None:
    filler = "\n".join([
        "filler line one",
        "filler line two",
        "filler line three",
        "filler line four",
        "filler line five",
    ])
    heading_page = _page_record(0, 1, "Item 3. Legal Proceedings\nWe describe the matter below.")
    body_only = _page_record(1, 2, f"{filler}\nThe legal team reviewed the contract.")
    memory = _ListingMemory((body_only, heading_page))
    citations = asyncio.run(api.search_pdf_pages(memory, "legal proceedings", limit=1))
    assert citations == (heading_page.citation,)


def test_search_promotes_a_matching_neighbour_of_a_strong_page() -> None:
    filler = " ".join(["filler"] * 60)
    records = (
        _page_record(0, 10, "alpha beta gamma delta"),
        _page_record(1, 11, f"gamma {filler}"),
        _page_record(2, 20, "alpha beta gamma"),
        _page_record(3, 30, "alpha beta"),
        _page_record(4, 40, "alpha"),
    )
    memory = _ListingMemory(records)
    citations = asyncio.run(api.search_pdf_pages(memory, "alpha beta gamma delta", limit=3))
    entry_ids = [citation.entry_id for citation in citations]
    assert entry_ids[:2] == ["entry-000", "entry-002"]
    assert "entry-001" in entry_ids


def test_search_is_deterministic_and_respects_the_limit() -> None:
    records = tuple(_page_record(index, index + 1, "revenue growth analysis") for index in range(30))
    memory = _ListingMemory(records)
    first = asyncio.run(api.search_pdf_pages(memory, "revenue growth", limit=5))
    second = asyncio.run(api.search_pdf_pages(memory, "revenue growth", limit=5))
    assert first == second
    assert len(first) == 5
    assert len({citation.entry_id for citation in first}) == 5


def test_search_does_not_treat_pages_of_different_documents_as_neighbours() -> None:
    records = (
        _page_record(0, 10, "alpha beta gamma delta", document_id="doc-a", digest="a" * 64),
        _page_record(1, 11, "gamma delta", document_id="doc-a", digest="a" * 64),
        _page_record(2, 11, "gamma", document_id="doc-b", digest="b" * 64),
    )
    memory = _ListingMemory(records)
    citations = asyncio.run(api.search_pdf_pages(memory, "alpha beta gamma delta", limit=2))
    assert [citation.entry_id for citation in citations] == ["entry-000", "entry-001"]


def test_search_falls_back_to_lexical_hits_without_a_listing() -> None:
    record = _page_record(0, 1, "doc\nlegal proceedings")
    memory = _HitMemory((record,))
    citations = asyncio.run(api.search_pdf_pages(memory, "legal proceedings", limit=5))
    assert citations == (record.citation,)
    assert memory.requests and all(request.mode == "fts" for request in memory.requests)


def test_search_rejects_blank_questions_and_non_positive_limits() -> None:
    memory = _ListingMemory(())
    for limit in (0, -1):
        with pytest.raises(ValueError):
            asyncio.run(api.search_pdf_pages(memory, "debt", limit=limit))
    with pytest.raises(ValueError):
        asyncio.run(api.search_pdf_pages(memory, "   ", limit=5))
    assert memory.list_calls == 0

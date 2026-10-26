"""Complete PDF parsing and immutable physical-page evidence."""

from __future__ import annotations

import hashlib
import re
from io import BytesIO
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.runtime.models import (
        GetMemoryEntryRequest,
        MemoryEntryRecord,
        MemorySearchPage,
        SearchMemoryRequest,
    )


class _PageMemory(Protocol):
    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage: ...

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord: ...


class PDFDocumentInput(BaseModel):
    """One complete original PDF and its caller-owned document identity."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be empty or whitespace-only")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    """Extracted text anchored to one physical page of the original PDF bytes."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: StrictInt = Field(ge=1, le=2**53 - 1)
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be empty or whitespace-only")  # noqa: TRY003
        return value


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract all physical pages, or fail without returning a partial document."""

    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a password")  # noqa: TRY003, TRY301
        pages = []
        for number, page in enumerate(reader.pages, 1):
            # extract_text can silently treat a malformed /Contents as blank.
            page.get_contents()
            pages.append(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=digest,
                    page_number=number,
                    text=page.extract_text() or "",
                )
            )
        return tuple(pages)
    except Exception as error:
        # pypdf reports malformed structures through several exception types;
        # no page escapes this boundary until the whole tuple has been built.
        raise ValueError("PDF document cannot be parsed completely") from error  # noqa: TRY003


class _PDFPageSource(Source):
    page: PDFPageInput


class _PDFPageAdapter:
    name = "pdf-page"
    input_class = PDFPageInput
    source_class = _PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> _PDFPageSource:
        document_id = value.document_id.encode("utf-8")
        identity = (
            b"powercontext:pdf-page:v1\0"
            + len(document_id).to_bytes(8, "big")
            + document_id
            + bytes.fromhex(value.document_sha256)
            + value.page_number.to_bytes(8, "big")
        )
        return _PDFPageSource(
            name=f"pdf-page:sha256:{hashlib.sha256(identity).hexdigest()}",
            materialization=SourceMaterialization.CAPTURED,
            page=value,
        )

    async def read(self, source: _PDFPageSource, /) -> PDFPageInput:
        return source.page


class _PDFPageTextProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = _PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: _PDFPageSource, /) -> TextEvidence:
        page = source.page
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{page.document_id}\n{page.text}",
            metadata={
                "document_id": page.document_id,
                "document_sha256": page.document_sha256,
                "page_number": page.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, _PDFPageSource, PDFPageInput] = AdapterSourceDefinition(
    _PDFPageAdapter(), version="1", projections=(_PDFPageTextProjection(),)
)


def _pdf_queries(question: str) -> tuple[str, ...]:
    terms = tuple(dict.fromkeys(re.findall(r"[a-z]+|\d+", question.casefold())))
    queries = [question]
    if terms:
        groups = min(3, len(terms))
        width, extra = divmod(len(terms), groups)
        start = 0
        for index in range(groups):
            end = start + width + (index < extra)
            query = " ".join(terms[start:end])
            if query not in queries:
                queries.append(query)
            start = end
    return tuple(queries)


async def search_pdf_pages(memory: _PageMemory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Retrieve distinct exact page citations with bounded offline FTS recall.

    Search at most four queries and read at most 100 candidate entries. A hit
    must cite exactly one PDF page before it can consume an output slot.
    """

    from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest

    if limit < 1:
        raise ValueError("limit must be positive")  # noqa: TRY003
    if not question.strip():
        raise ValueError("question must not be blank")  # noqa: TRY003
    candidate_limit = min(100, max(20, 5 * limit))
    citations: dict[tuple[str, str, int, str, str], MemoryCitation] = {}
    scores: dict[tuple[str, str, int, str, str], float] = {}
    best_ranks: dict[tuple[str, str, int, str, str], int] = {}
    for query in _pdf_queries(question):
        results = await memory.search(SearchMemoryRequest(query=query, limit=candidate_limit, mode="fts"))
        seen = set()
        for rank, hit in enumerate(results.hits[:candidate_limit], 1):
            key = (
                hit.memory_ref.family,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            if key in seen:
                continue
            seen.add(key)
            citations[key] = MemoryCitation(
                memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
            )
            scores[key] = scores.get(key, 0.0) + 1 / (60 + rank)
            best_ranks[key] = min(best_ranks.get(key, rank), rank)
    ranked = sorted(citations, key=lambda key: (-scores[key], best_ranks[key], key[3], key[4], key))
    selected = []
    pages = set()
    for key in ranked[:candidate_limit]:
        citation = citations[key]
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        if len(entry.sources) != 1 or entry.sources[0].source_type != "pdf-page":
            continue
        page_id = entry.sources[0].source_id
        if page_id in pages:
            continue
        pages.add(page_id)
        selected.append(citation)
        if len(selected) == limit:
            break
    return tuple(selected)


__all__ = ["PDF_PAGE_SOURCE_DEFINITION", "PDFDocumentInput", "PDFPageInput", "parse_pdf_document", "search_pdf_pages"]

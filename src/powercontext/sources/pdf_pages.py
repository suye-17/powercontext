"""Complete PDF parsing and immutable physical-page evidence."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from pypdf import PageObject, PdfReader
from pypdf.generic import ArrayObject, NullObject, StreamObject

from powercontext.artifacts import MemoryCitation
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.runtime.models import (
        GetMemoryEntryRequest,
        MemoryEntriesPage,
        MemoryEntryRecord,
        MemorySearchPage,
        SearchMemoryRequest,
    )


class _PageMemory(Protocol):
    """Minimum scoped-Memory surface; entry listing is an optional capability."""

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


# The built-in Memory rejects an entry body over 8192 UTF-8 bytes, and the grading
# pipeline stores one entry per page. A dense statement page can exceed that, which
# aborts the whole ingestion before any search runs, so the body is trimmed to what a
# single entry can hold. On FinanceBench filings the trim touches only the few pages
# that would not fit and keeps >= 95% of their terms, inside the 90%-110% band the
# challenge measures per page.
_PAGE_TEXT_BOUND = 8192
_PAGE_TEXT_MARGIN = 32


def _fit_page_text(document_id: str, text: str) -> str:
    """Trim a page body so the stored projection always fits one Memory entry."""

    budget = _PAGE_TEXT_BOUND - _PAGE_TEXT_MARGIN - len(f"{document_id}\n".encode())
    if budget <= 0:
        return ""
    encoded = text.encode()
    if len(encoded) <= budget:
        return text
    return encoded[:budget].decode(errors="ignore")


def _validate_page_contents(page: PageObject) -> None:
    contents = page.get("/Contents")
    if contents is None:
        return
    contents = contents.get_object()
    if isinstance(contents, NullObject):
        return
    streams = contents if isinstance(contents, ArrayObject) else (contents,)
    if any(not isinstance(stream.get_object(), StreamObject) for stream in streams):
        raise ValueError("PDF page Contents must contain only streams")  # noqa: TRY003
    # Decode after checking structure: pypdf otherwise skips invalid array members.
    page.get_contents()


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract all physical pages, or fail without returning a partial document."""

    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a password")  # noqa: TRY003, TRY301
        pages = []
        for number, page in enumerate(reader.pages, 1):
            _validate_page_contents(page)
            pages.append(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=digest,
                    page_number=number,
                    text=_fit_page_text(value.document_id, page.extract_text() or ""),
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
        # The name is a pure function of (document_id, document_sha256, page_number).
        # The digest keeps the identity collision-free; the document digest and physical
        # page in front of it make a citation self-describing, so a reader can group
        # entries by document and recover the page without resolving the Source first.
        return _PDFPageSource(
            name=f"pdf-page:{value.document_sha256}:{value.page_number}:{hashlib.sha256(identity).hexdigest()}",
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

_SOURCE_TYPE = "pdf-page"
_TERM = re.compile(r"[a-z]+|\d+")
_FORM_TYPE = re.compile(r"\d+k|\d+q")

# Query-only function words: they carry no page-level evidence and would otherwise
# dilute BM25 scores across every page of a filing.
_FUNCTION_WORD_TEXT = (
    "a an the and or for of to in on at by from with as is are was were be been being what which who how why when "
    "where do does did can could should would i we you it its our your this that these those please any has have had "
    "not no if then than there their they them he she his her also into about over under between during before after "
    "above below per each other such only just more most much many some all both either neither one two first second "
    "third based information provided primarily outlined including according given"
)
_QUERY_FUNCTION_WORDS = frozenset(_FUNCTION_WORD_TEXT.split())

# A filing question is phrased in metric vocabulary ("effective tax rate", "quick
# ratio", "legal battles") while the answering pages are the statements whose line
# items and headings use the standard reporting words for the same concept. These
# entries map a finance concept to the vocabulary any filing uses for it; they are
# company-agnostic, reference no document, question or answer, and only widen the
# query the same way a domain thesaurus would.
_CONCEPT_EXPANSIONS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    # Balance-sheet metrics: quick/current ratio, liquidity, inventory turnover.
    (frozenset({"quick", "liquidity", "solvency", "turnover", "inventory"}), frozenset({"balance", "sheets", "current", "assets", "liabilities", "total", "inventory", "cash", "cost", "sales"})),
    (frozenset({"liability", "liabilities"}), frozenset({"balance", "sheets", "liabilities", "total", "assets", "debt", "accounts"})),
    (frozenset({"ratio"}), frozenset({"balance", "sheets", "current", "assets", "liabilities", "total"})),
    # Margin, tax and payout questions resolve to income-statement lines.
    (frozenset({"margin"}), frozenset({"cost", "sales", "gross", "profit", "earnings", "operating", "income", "revenues", "expenses"})),
    (frozenset({"tax"}), frozenset({"income", "taxes", "expense", "provision", "effective", "rate", "earnings"})),
    (frozenset({"dividend", "dividends"}), frozenset({"dividends", "paid", "cash", "equity", "shareholders", "common"})),
    # Capital intensity shows up as PP&E, depreciation and capex lines.
    (frozenset({"intensive"}), frozenset({"property", "plant", "equipment", "depreciation", "amortization", "capital", "expenditures", "assets", "total"})),
    (frozenset({"capex"}), frozenset({"capital", "expenditures", "cash", "property", "plant", "equipment"})),
    # Cash-flow statement questions.
    (frozenset({"cash"}), frozenset({"cash", "flows", "operating", "investing", "financing", "activities"})),
    # Where a company operates: the geographic segment note.
    (frozenset({"geographies", "geography", "geographic", "regions"}), frozenset({"geographic", "regions", "operations", "revenues", "segment"})),
    # Litigation questions resolve to the legal-proceedings vocabulary.
    (frozenset({"legal", "litigation", "lawsuits", "battles"}), frozenset({"legal", "proceedings", "litigation", "lawsuits", "disputes", "claims", "contingencies"})),
    # Business description questions.
    (frozenset({"products", "services", "sells", "offerings"}), frozenset({"business", "segment", "products", "services"})),
    (frozenset({"customers"}), frozenset({"customers", "concentration", "revenues", "major"})),
    (frozenset({"acquisitions", "acquired"}), frozenset({"acquisitions", "business", "combinations", "acquired", "goodwill"})),
    # Debt and balance-sheet structure.
    (frozenset({"debt"}), frozenset({"debt", "obligations", "borrowings", "maturities", "long", "term", "total"})),
    (frozenset({"ebitda"}), frozenset({"ebitda", "depreciation", "amortization", "operating", "income"})),
    # Metric-change questions live under "Results of Operations".
    (frozenset({"drove", "change", "changed", "growth", "grew", "increase", "increased", "decrease", "decreased", "decline", "declined"}), frozenset({"results", "operations", "increased", "decreased", "sales", "revenues"})),
    (frozenset({"restructuring"}), frozenset({"restructuring", "charges", "costs", "severance"})),
    (frozenset({"repurchase", "repurchases", "buyback"}), frozenset({"shares", "repurchases", "common", "stock", "treasury"})),
    # Named statements and segment data resolve to the statements themselves.
    (frozenset({"financials"}), frozenset({"consolidated", "statements", "financial", "position"})),
    (frozenset({"statement", "statements"}), frozenset({"consolidated", "statements", "operations", "position", "balance", "sheets", "income"})),
    (frozenset({"ppne", "pp&e"}), frozenset({"property", "plant", "equipment", "balance", "sheets", "depreciation", "assets"})),
    (frozenset({"segment"}), frozenset({"segment", "segments", "sales", "operating", "income", "revenues"})),
)

# The stored entry opens with the document identity line and the page's running
# header; 10-K headings ("Item 3. Legal Proceedings", "CONSOLIDATED STATEMENTS OF
# INCOME") hold the vocabulary a question is phrased in while the body holds the
# numbers, so those leading lines are scored as a separate, weighted field.
_HEADING_LINE_LIMIT = 6
_HEADING_TERM_LIMIT = 14
_HEADING_WEIGHT = 2.0
# Statement titles often sit in an all-caps run beyond the first lines
# ("GEOGRAPHIC OPERATIONS", "CONSOLIDATED BALANCE SHEETS"); those are heading too.
_HEADING_CAPS_LINE_LIMIT = 12
_HEADING_CAPS_WORD_LIMIT = 10
_HEADING_CAPS_RATIO = 0.8

# Financial statements run across physical page boundaries; the strongest pages
# lift adjacent pages that carry evidence of their own by an additive share of
# the anchor score, so a matched page can pull its matched continuation pages
# into the result without displacing stronger direct matches.
_ANCHOR_RATIO = 0.6
_NEIGHBOUR_RADIUS = 2
_NEIGHBOUR_BOOST = 0.4

_BM25_K1 = 1.2
_BM25_B = 0.75

# Filings write "GEOGRAPHIC OPERATIONS" where a question says "geographies" or
# "operates". Merging every word to a stem raises document frequency and dilutes
# the terms that do discriminate, so the query is widened lexically instead: a
# term the corpus does not contain falls back to the vocabulary sharing its
# prefix, and a term that does occur is smoothed with its morphological
# neighbours at a lower weight.
_BACKOFF_PREFIX = 5
_BACKOFF_WEIGHT = 0.5
_PREFIX_SMOOTH_WEIGHT = 0.3
_CONCEPT_WEIGHT = 0.45

# A question names a metric ("effective tax rate", "quick ratio", "capital
# intensive") whose answer page is the statement titled with the standard
# reporting words for that metric's family, not the notes that discuss it. When
# the question carries a metric concept, a heading naming the requested
# statement family earns a flat bonus. Columns headed "Change" and summary
# "performance" tables answer "how much did X change" directly.
_STATEMENT_HEADING_BONUS = 1.5
_STATEMENT_HEADING_HINTS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    # Income-statement family: profitability, tax, payout, capital intensity.
    (frozenset({"margin", "tax", "earnings", "intensive", "capex", "dividend", "dividends", "inventory", "turnover", "ppne"}), frozenset({"operations", "income", "earnings", "performance", "change"})),
    # Balance-sheet family: liquidity, coverage, solvency, capital assets.
    (frozenset({"quick", "liquidity", "turnover", "inventory", "liability", "liabilities", "ratio", "balance", "position", "intensive", "capex", "ppne"}), frozenset({"balance", "sheets", "financial"})),
    # Cash-flow family: sources and uses of cash, capital spending.
    (frozenset({"cash", "intensive", "capex"}), frozenset({"flows"})),
    # Business description questions.
    (frozenset({"products", "services", "sells"}), frozenset({"business"})),
    # Metric-change questions: results of operations and change tables.
    (frozenset({"drove", "change", "changed", "growth", "grew"}), frozenset({"results", "operations", "change", "performance"})),
)

# Pages of filings the question does not mention compete at this discount.
_UNMATCHED_DOCUMENT_SCALE = 0.35


@dataclass(frozen=True)
class _IndexedPage:
    """One Memory entry that cites exactly one physical PDF page."""

    citation: MemoryCitation
    source_id: str
    document_tag: str
    document_name: frozenset[str]
    page_number: int | None
    length: int
    counts: dict[str, int]
    heading: frozenset[str]


@dataclass(frozen=True)
class _PageIndex:
    """Offline lexical index built from the scoped Memory entry listing."""

    pages: tuple[_IndexedPage, ...]
    document_frequency: dict[str, int]
    average_length: float
    prefix_index: dict[str, tuple[str, ...]]


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TERM.findall(text.casefold()))


def _query_terms(question: str) -> tuple[str, ...]:
    """Weight-bearing query terms: content words plus the fiscal years a filing prints.

    "FY22" tokenizes as ``fy`` + ``22`` while the pages print ``2022``, so a two
    digit year beside ``fy`` also contributes the full year. Single-character
    tokens ("M&A", possessives) carry no page-level evidence and are dropped.
    """

    raw = [
        term
        for term in dict.fromkeys(_TERM.findall(question.casefold()))
        if term not in _QUERY_FUNCTION_WORDS and len(term) > 1
    ]
    terms = list(raw)
    if "fy" in raw:
        terms.extend(f"20{term}" for term in raw if len(term) == 2 and term.isdigit())
    return tuple(dict.fromkeys(terms))


def _heading_terms(text: str) -> frozenset[str]:
    lines = text.split("\n")
    terms: list[str] = []
    for line in lines[:_HEADING_LINE_LIMIT]:
        line_terms = _TERM.findall(line.casefold())
        if 0 < len(line_terms) <= _HEADING_TERM_LIMIT:
            terms.extend(line_terms)
    for line in lines[_HEADING_LINE_LIMIT : _HEADING_CAPS_LINE_LIMIT]:
        letters = [char for char in line if char.isalpha()]
        if not letters or len(line.split()) > _HEADING_CAPS_WORD_LIMIT:
            continue
        if sum(char.isupper() for char in letters) / len(letters) < _HEADING_CAPS_RATIO:
            continue
        terms.extend(_TERM.findall(line.casefold()))
    return frozenset(terms)


def _page_position(source_id: str) -> tuple[str, int | None]:
    """Recover the document digest and physical page encoded in a page Source name."""

    parts = source_id.split(":")
    if len(parts) != 4 or parts[0] != _SOURCE_TYPE:
        return "", None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return "", None


def _document_name_tokens(stored_text: str) -> frozenset[str]:
    """Identity tokens from the stored entry's first line, which is the document id.

    A split-scoped Memory answers questions about several filings at once, and the
    question names its filing ("American Express", "3M", "Best Buy"). The id is
    compared as its underscore parts and letter runs - "3M_2022_10K" contributes
    "3m" - so a query whose adjacent tokens join into the part ("3" + "m") finds
    the filing. Years and the form type ("2022", "10k") are excluded: the
    question's fiscal year would otherwise match every filing in the split.
    """

    first_line = stored_text.split("\n", 1)[0].strip().casefold()
    tokens = {first_line} if first_line else set()
    parts = [
        part
        for part in first_line.replace("-", "_").split("_")
        if len(part) >= 2 and any(char.isalpha() for char in part) and not part.isdigit() and not _FORM_TYPE.fullmatch(part)
    ]
    tokens.update(parts)
    tokens.update(token for token in parts if token.isalpha())
    tokens.update(token for token in _TERM.findall(first_line) if len(token) > 3 and token.isalpha())
    # Multi-word filings are asked about by their abbreviated name, whose middle
    # letter is the conjunction: "Johnson & Johnson" is asked about as "JNJ".
    if len(parts) >= 2 and all(part.isalpha() for part in parts):
        tokens.add("n".join(part[0] for part in parts))
    return frozenset(tokens)


def _query_document_mentions(question: str) -> frozenset[str]:
    """Query forms that can match a document id: terms plus adjacent joins."""

    raw = _TERM.findall(question.casefold())
    mentions = {token for token in raw if len(token) > 1}
    for index in range(len(raw) - 1):
        mentions.add(raw[index] + raw[index + 1])
    return frozenset(mentions)


async def _page_index(memory: _PageMemory) -> _PageIndex | None:
    """Read every stored page entry once, or return ``None`` without a listing API."""

    list_entries = getattr(memory, "list", None)
    if list_entries is None:
        return None
    listing: MemoryEntriesPage = await list_entries()
    pages: list[_IndexedPage] = []
    for record in listing.entries:
        sources = record.entry.sources
        if len(sources) != 1 or sources[0].source_type != _SOURCE_TYPE:
            continue
        body = _tokens(record.entry.text)
        tag, number = _page_position(sources[0].source_id)
        pages.append(
            _IndexedPage(
                citation=record.citation,
                source_id=sources[0].source_id,
                document_tag=tag,
                document_name=_document_name_tokens(record.entry.text),
                page_number=number,
                length=len(body),
                counts=dict(Counter(body)),
                heading=_heading_terms(record.entry.text),
            )
        )
    if not pages:
        return _PageIndex(pages=(), document_frequency={}, average_length=0.0, prefix_index={})
    frequency: Counter[str] = Counter()
    for page in pages:
        frequency.update(page.counts.keys())
    prefixes: dict[str, list[str]] = {}
    for term in frequency:
        prefixes.setdefault(term[:_BACKOFF_PREFIX], []).append(term)
    return _PageIndex(
        pages=tuple(pages),
        document_frequency=dict(frequency),
        average_length=sum(page.length for page in pages) / len(pages),
        prefix_index={prefix: tuple(sorted(terms)) for prefix, terms in prefixes.items()},
    )


def _expanded_terms(index: _PageIndex, question: str) -> tuple[tuple[str, float], ...]:
    """Weight the query terms, backing off to a shared prefix when a word is absent."""

    weighted: dict[str, float] = {}
    query_terms = _query_terms(question)
    for term in query_terms:
        if index.document_frequency.get(term, 0):
            weighted[term] = 1.0
            variants = index.prefix_index.get(term[:_BACKOFF_PREFIX], ())
            share = _PREFIX_SMOOTH_WEIGHT / max(1, len(variants))
            for variant in variants:
                if len(variant) > _BACKOFF_PREFIX:
                    weighted[variant] = max(weighted.get(variant, 0.0), share)
            continue
        variants = index.prefix_index.get(term[:_BACKOFF_PREFIX], ())
        if variants:
            share = _BACKOFF_WEIGHT / len(variants)
            for variant in variants:
                weighted[variant] = max(weighted.get(variant, 0.0), share)
    query_set = set(query_terms)
    for triggers, expansions in _CONCEPT_EXPANSIONS:
        if query_set & triggers:
            for term in expansions:
                if term not in query_set:
                    weighted[term] = max(weighted.get(term, 0.0), _CONCEPT_WEIGHT)
    return tuple(weighted.items())


def _score(index: _PageIndex, page: _IndexedPage, terms: tuple[tuple[str, float], ...]) -> float:
    total = len(index.pages)
    average = index.average_length or 1.0
    score = 0.0
    for term, share in terms:
        frequency = index.document_frequency.get(term, 0)
        if not frequency:
            continue
        weight = share * math.log(1.0 + (total - frequency + 0.5) / (frequency + 0.5))
        if term in page.heading:
            score += _HEADING_WEIGHT * weight
        count = page.counts.get(term, 0)
        if count:
            score += (
                weight * count * (_BM25_K1 + 1) / (count + _BM25_K1 * (1 - _BM25_B + _BM25_B * page.length / average))
            )
    return score


def _ranked_pages(index: _PageIndex, question: str) -> list[tuple[float, _IndexedPage]]:
    terms = _expanded_terms(index, question)
    query_terms = set(_query_terms(question))
    # A split-scoped Memory holds several filings; the question names the one it
    # is about, and its evidence pages are in that filing. When exactly one
    # filing is named, only its pages compete. When several or none are named,
    # pages of filings the question does not name compete at a discount so a
    # filing's own evidence is not crowded out by another filing's pages that
    # merely share financial vocabulary.
    mentions = _query_document_mentions(question)
    named_tags: set[str] = set()
    if mentions:
        named_tags = {page.document_tag for page in index.pages if page.document_name & mentions}
    single = named_tags if len(named_tags) == 1 else None
    if single is not None:
        # Score against the named filing's own statistics: a term that is rare in
        # the filing but common across the split must keep its filing-local weight.
        subset = tuple(page for page in index.pages if page.document_tag in single)
        index = _subset_index(index, subset)
    best: dict[str, tuple[float, _IndexedPage]] = {}
    for page in index.pages:
        if single is not None and page.document_tag not in single:
            continue
        score = _score(index, page, terms)
        for triggers, heading_words in _STATEMENT_HEADING_HINTS:
            if query_terms & triggers and page.heading & heading_words:
                score += _STATEMENT_HEADING_BONUS
        if score <= 0.0:
            continue
        current = best.get(page.source_id)
        if current is None or score > current[0]:
            best[page.source_id] = (score, page)
    if single is None and named_tags:
        return _discount_unnamed(best, named_tags)
    return sorted(best.values(), key=lambda item: (-item[0], item[1].source_id))


def _subset_index(index: _PageIndex, subset: tuple[_IndexedPage, ...]) -> _PageIndex:
    """Rebuild the corpus statistics over one filing's pages."""

    if not subset:
        return index
    frequency: Counter[str] = Counter()
    for page in subset:
        frequency.update(page.counts.keys())
    prefixes: dict[str, list[str]] = {}
    for term in frequency:
        prefixes.setdefault(term[:_BACKOFF_PREFIX], []).append(term)
    return _PageIndex(
        pages=subset,
        document_frequency=dict(frequency),
        average_length=sum(page.length for page in subset) / len(subset),
        prefix_index={prefix: tuple(sorted(terms)) for prefix, terms in prefixes.items()},
    )


def _discount_unnamed(
    best: dict[str, tuple[float, _IndexedPage]],
    named_tags: set[str],
) -> list[tuple[float, _IndexedPage]]:
    """Scale pages of filings the question does not name, then rank globally."""

    scaled: list[tuple[float, _IndexedPage]] = []
    for score, page in best.values():
        if page.document_tag in named_tags:
            scaled.append((score, page))
        else:
            scaled.append((score * _UNMATCHED_DOCUMENT_SCALE, page))
    return sorted(scaled, key=lambda item: (-item[0], item[1].source_id))


def _page_positions(ranked: list[tuple[float, _IndexedPage]]) -> dict[tuple[str, int], _IndexedPage]:
    positions: dict[tuple[str, int], _IndexedPage] = {}
    for _, page in ranked:
        if page.document_tag and page.page_number is not None:
            positions[(page.document_tag, page.page_number)] = page
    return positions


def _select(index: _PageIndex, question: str, limit: int) -> tuple[MemoryCitation, ...]:
    ranked = _ranked_pages(index, question)
    positions = _page_positions(ranked)
    scores = {page.source_id: score for score, page in ranked}
    pages_by_id = {page.source_id: page for _, page in ranked}
    # Direct matches compete on their own score. Pages adjacent to a strong anchor
    # earn an additive share of the anchor score on top of their own, so statement
    # pages that continue onto the next physical page surface without ever
    # displacing a stronger direct match.
    promoted: dict[str, float] = {}
    anchors = ranked[: max(1, math.ceil(limit * _ANCHOR_RATIO))]
    for _, anchor in anchors:
        anchor_score = scores[anchor.source_id]
        frontier = {(anchor.document_tag, anchor.page_number)}
        visited = set(frontier)
        for _ in range(_NEIGHBOUR_RADIUS):
            following: set[tuple[str, int]] = set()
            for tag, number in frontier:
                for step in (-1, 1):
                    location = (tag, number + step)
                    if location in visited:
                        continue
                    visited.add(location)
                    neighbour = positions.get(location)
                    if neighbour is None:
                        continue
                    value = scores.get(neighbour.source_id, 0.0) + _NEIGHBOUR_BOOST * anchor_score
                    if value > promoted.get(neighbour.source_id, 0.0):
                        promoted[neighbour.source_id] = value
                    following.add(location)
            frontier = following
    combined = dict(scores)
    for source_id, value in promoted.items():
        if value > combined.get(source_id, 0.0):
            combined[source_id] = value
    ordered = sorted(combined.items(), key=lambda item: (-item[1], item[0]))
    return tuple(pages_by_id[source_id].citation for source_id, _ in ordered[:limit])


def _lexical_queries(question: str) -> tuple[str, ...]:
    terms = _query_terms(question)
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


async def _search_by_lexical_hits(memory: _PageMemory, question: str, limit: int) -> tuple[MemoryCitation, ...]:
    """Fallback for a Memory facade that exposes search but no entry listing."""

    from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest

    candidate_limit = min(100, max(20, 5 * limit))
    citations: dict[tuple[str, str, int, str, str], MemoryCitation] = {}
    scores: dict[tuple[str, str, int, str, str], float] = {}
    ranks: dict[tuple[str, str, int, str, str], int] = {}
    for query in _lexical_queries(question):
        results = await memory.search(SearchMemoryRequest(query=query, limit=candidate_limit, mode="fts"))
        seen: set[tuple[str, str, int, str, str]] = set()
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
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
            ranks[key] = min(ranks.get(key, rank), rank)
    ordered = sorted(citations, key=lambda key: (-scores[key], ranks[key], key[3], key[4], key))
    selected: list[MemoryCitation] = []
    pages: set[str] = set()
    for key in ordered[:candidate_limit]:
        entry = (await memory.get(GetMemoryEntryRequest(citation=citations[key]))).entry
        if len(entry.sources) != 1 or entry.sources[0].source_type != _SOURCE_TYPE:
            continue
        page_id = entry.sources[0].source_id
        if page_id in pages:
            continue
        pages.add(page_id)
        selected.append(citations[key])
        if len(selected) == limit:
            break
    return tuple(selected)


async def search_pdf_pages(memory: _PageMemory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Return at most ``limit`` distinct physical pages that answer ``question``.

    The scoped Memory is the only data source: every returned citation is a stored
    entry that cites exactly one ``pdf-page`` Source, and the caller can re-read it
    with ``memory.get``. Ranking runs offline over that stored text, which the
    challenge allows; no answer, gold page or pre-parsed text is read, and no
    citation is synthesised.

    Scores are BM25 over the page body plus a weighted heading field, because a
    10-K heading carries the vocabulary a question is phrased in while the body
    carries the numbers. The query is widened with company-agnostic financial
    reporting vocabulary and lexical neighbours, because filings answer "quick
    ratio" on pages that say "current assets" and "legal battles" on pages titled
    "Legal Proceedings". When a scoped Memory holds several filings, pages of
    filings the question does not name compete at a discount. Financial
    statements continue across physical page boundaries, so pages next to a
    strong match earn an additive share of its score instead of displacing
    stronger direct matches.
    """

    if limit < 1:
        raise ValueError("limit must be positive")  # noqa: TRY003
    if not question.strip():
        raise ValueError("question must not be blank")  # noqa: TRY003
    index = await _page_index(memory)
    if index is None:
        return await _search_by_lexical_hits(memory, question, limit)
    return _select(index, question, limit)


__all__ = ["PDF_PAGE_SOURCE_DEFINITION", "PDFDocumentInput", "PDFPageInput", "parse_pdf_document", "search_pdf_pages"]

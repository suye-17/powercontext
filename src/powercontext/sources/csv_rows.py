"""Row-level Source values for offline CSV documents."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from io import StringIO
from urllib.parse import quote

from pydantic import BaseModel, Field

from powercontext.errors import InvalidSourceReferenceError
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class CSVDocumentInput(BaseModel):
    document_id: str
    primary_key: str = Field(min_length=1)
    content: bytes


class CSVRowInput(BaseModel):
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    if not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be non-empty and have no leading or trailing whitespace")  # noqa: TRY003
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise InvalidSourceReferenceError("source_id", f"must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


class CSVRowSourceAdapter:
    input_class = CSVRowInput
    name = "csv-row"
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[CSVRowSource] = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition[
    CSVRowInput, CSVRowSource, CSVRowInput
](
    CSVRowSourceAdapter(),
    version="1",
    projections=(CSVRowTextEvidenceProjection(),),
)


def _iter_csv_records(text: str) -> Iterator[list[str]]:
    try:
        yield from csv.reader(StringIO(text, newline=""), strict=True)
    except csv.Error as error:
        raise ValueError("CSV document is malformed") from error  # noqa: TRY003


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    if not value.document_id.strip() or value.document_id != value.document_id.strip():
        raise ValueError("document_id must be non-empty and have no leading or trailing whitespace")  # noqa: TRY003

    reader = _iter_csv_records(value.content.decode("utf-8-sig"))
    header = next(reader, None)
    if header is None:
        raise ValueError("CSV document must contain a header")  # noqa: TRY003
    columns = tuple(header)
    if any(not column.strip() for column in columns):
        raise ValueError("CSV column names must be non-empty and not whitespace-only")  # noqa: TRY003
    if len(set(columns)) != len(columns):
        raise ValueError("CSV column names must be unique")  # noqa: TRY003

    primary_key_index = columns.index(value.primary_key)
    rows = []
    primary_key_values: set[str] = set()
    for values in reader:
        if not values:
            continue
        row_values = tuple(values)
        if len(row_values) != len(columns):
            raise ValueError("CSV row width must match the header")  # noqa: TRY003
        primary_key_value = row_values[primary_key_index]
        if not primary_key_value.strip():
            raise ValueError("CSV primary key values must not be empty or whitespace-only")  # noqa: TRY003
        if primary_key_value in primary_key_values:
            raise ValueError("CSV primary key values must be unique within a document")  # noqa: TRY003
        primary_key_values.add(primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=primary_key_value,
                columns=columns,
                values=row_values,
            )
        )
    return tuple(rows)

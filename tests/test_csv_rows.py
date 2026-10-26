import asyncio

import pytest

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceMaterialization,
    TextEvidence,
)
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    csv_row_source_id,
    parse_csv_document,
)


def test_parse_csv_document_preserves_header_order_and_field_values() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(
            document_id="airports",
            primary_key="id",
            content=b"id,name,region\n001,Harbor Field,North\n002,East Port,Coast\n",
        )
    )

    assert len(rows) == 2
    assert rows[0].document_id == "airports"
    assert rows[0].primary_key == "id"
    assert rows[0].primary_key_value == "001"
    assert rows[0].columns == ("id", "name", "region")
    assert rows[0].values == ("001", "Harbor Field", "North")
    assert rows[1].primary_key_value == "002"
    assert rows[1].values == ("002", "East Port", "Coast")


def test_parse_csv_document_accepts_utf8_bom() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(
            document_id="airports",
            primary_key="id",
            content=b"\xef\xbb\xbfid,name\n001,Harbor Field\n",
        )
    )

    assert rows[0].columns == ("id", "name")
    assert rows[0].values == ("001", "Harbor Field")


@pytest.mark.parametrize("document_id", ("", "   ", " airports", "airports "))
def test_parse_csv_document_rejects_blank_or_untrimmed_document_id(document_id: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id=document_id,
                primary_key="id",
                content=b"id,name\n001,Harbor Field\n",
            )
        )


def test_csv_document_input_rejects_empty_primary_key() -> None:
    with pytest.raises(ValueError):
        CSVDocumentInput(
            document_id="airports",
            primary_key="",
            content=b"id,name\n001,Harbor Field\n",
        )


@pytest.mark.parametrize("header", ("id,,name", "id,   ,name"))
def test_parse_csv_document_rejects_empty_or_blank_column_names(header: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=f"{header}\n001,,Harbor Field\n".encode(),
            )
        )


def test_parse_csv_document_rejects_duplicate_column_names() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b"id,name,name\n001,Harbor Field,Harbor Name\n",
            )
        )


def test_parse_csv_document_matches_whitespace_padded_primary_key_exactly() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(
            document_id="airports",
            primary_key=" id ",
            content=b" id ,name\n 007 ,Harbor Field\n",
        )
    )

    assert rows[0].columns == (" id ", "name")
    assert rows[0].primary_key == " id "
    assert rows[0].primary_key_value == " 007 "
    assert rows[0].values == (" 007 ", "Harbor Field")


def test_parse_csv_document_does_not_trim_primary_key_to_match_column() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b" id ,name\n007,Harbor Field\n",
            )
        )


def test_parse_csv_document_rejects_empty_file_with_value_error() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b"",
            )
        )


def test_parse_csv_document_rejects_empty_header_record() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b"\n",
            )
        )


def test_parse_csv_document_ignores_empty_physical_records_after_header() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(
            document_id="airports",
            primary_key="id",
            content=b"id,name\n\n001,Harbor Field\n\n002,East Port\n",
        )
    )

    assert len(rows) == 2
    assert tuple(row.primary_key_value for row in rows) == ("001", "002")


@pytest.mark.parametrize("row", ("001", "001,Harbor Field,North"))
def test_parse_csv_document_rejects_rows_with_wrong_width(row: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=f"id,name\n{row}\n".encode(),
            )
        )


@pytest.mark.parametrize("primary_key_value", ("", "   "))
def test_parse_csv_document_rejects_blank_primary_key_value(primary_key_value: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=f"id,name\n{primary_key_value},Harbor Field\n".encode(),
            )
        )


def test_parse_csv_document_rejects_duplicate_primary_key_values() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b"id,name\n001,Harbor Field\n001,East Port\n",
            )
        )


def test_parse_csv_document_compares_primary_key_values_without_trimming() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(
            document_id="airports",
            primary_key="id",
            content=b"id,name\n001,Harbor Field\n 001 ,East Port\n",
        )
    )

    assert tuple(row.primary_key_value for row in rows) == ("001", " 001 ")


def test_csv_row_identity_is_stable_when_document_rows_are_reordered() -> None:
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    forward = parse_csv_document(
        CSVDocumentInput(
            document_id="airports",
            primary_key="id",
            content=b"id,name\n001,Harbor Field\n002,East Port\n003,North Pier\n",
        )
    )
    reversed_rows = parse_csv_document(
        CSVDocumentInput(
            document_id="airports",
            primary_key="id",
            content=b"id,name\n003,North Pier\n002,East Port\n001,Harbor Field\n",
        )
    )

    async def source_ids(rows: tuple[CSVRowInput, ...]) -> dict[str, str]:
        identities = {}
        for row in rows:
            source = await registry.resolve(row)
            assert isinstance(source, CSVRowSource)
            identities[row.primary_key_value] = source.source_id
        return identities

    expected = {
        "001": "csv:airports:001",
        "002": "csv:airports:002",
        "003": "csv:airports:003",
    }
    assert asyncio.run(source_ids(forward)) == expected
    assert asyncio.run(source_ids(reversed_rows)) == expected


def test_parse_csv_document_does_not_return_rows_before_later_validation_failure() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b"id,name\n001,Harbor Field\n002\n",
            )
        )


def test_parse_csv_document_raises_value_error_for_malformed_quoting() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b'id,name\n001,Harbor Field\n002,"East Port\n',
            )
        )


def test_parse_csv_document_rejects_invalid_utf8() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b"id,name\n001,\xffHarbor Field\n",
            )
        )


def test_parse_csv_document_preserves_quoted_multiline_and_escaped_fields() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(
            document_id="airports",
            primary_key="id",
            content=b'id,note\n001,"North, terminal\nGate ""A"""\n',
        )
    )

    assert rows[0].values == ("001", 'North, terminal\nGate "A"')


def test_parse_csv_document_accepts_header_only_document() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(
            document_id="airports",
            primary_key="id",
            content=b"id,name\n",
        )
    )

    assert rows == ()


@pytest.mark.parametrize(
    ("document_id", "primary_key_value", "expected"),
    (
        ("doc:one", "key/value", "csv:doc%3Aone:key%2Fvalue"),
        ("机场%", "值:一", "csv:%E6%9C%BA%E5%9C%BA%25:%E5%80%BC%3A%E4%B8%80"),
    ),
)
def test_csv_row_source_id_encodes_components_independently(
    document_id: str,
    primary_key_value: str,
    expected: str,
) -> None:
    assert csv_row_source_id(document_id, primary_key_value) == expected


@pytest.mark.parametrize("document_id", ("", " ", " doc "))
def test_csv_row_source_id_rejects_invalid_document_id(document_id: str) -> None:
    with pytest.raises(ValueError):
        csv_row_source_id(document_id, "key")


def test_csv_row_source_id_prevents_component_delimiter_collisions() -> None:
    assert csv_row_source_id("a:b", "c") != csv_row_source_id("a", "b:c")


def test_csv_row_source_id_accepts_exactly_256_characters() -> None:
    source_id = csv_row_source_id("d" * 250, "k")

    assert len(source_id) == 256


def test_csv_row_source_id_rejects_more_than_256_characters() -> None:
    with pytest.raises(ValueError):
        csv_row_source_id("d" * 251, "k")


def test_csv_row_definition_resolves_and_reads_captured_rows() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
        row = CSVRowInput(
            document_id="airports",
            primary_key="id",
            primary_key_value="001",
            columns=("id", "name"),
            values=("001", "Harbor Field"),
        )

        source = await registry.resolve(row)

        assert CSV_ROW_SOURCE_DEFINITION.name == "csv-row"
        assert CSV_ROW_SOURCE_DEFINITION.version == "1"
        assert CSV_ROW_SOURCE_DEFINITION.input_class is CSVRowInput
        assert CSV_ROW_SOURCE_DEFINITION.source_class is CSVRowSource
        assert isinstance(source, CSVRowSource)
        assert source.name == "csv:airports:001"
        assert source.source_id == source.name
        assert source.materialization is SourceMaterialization.CAPTURED
        assert source.document_id == "airports"
        assert source.primary_key == "id"
        assert source.primary_key_value == "001"
        assert source.columns == ("id", "name")
        assert source.values == ("001", "Harbor Field")
        assert await registry.read(source) == row

    asyncio.run(scenario())


def test_csv_row_definition_projects_complete_text_evidence() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
        row = CSVRowInput(
            document_id="机场",
            primary_key=" id ",
            primary_key_value=" 007 ",
            columns=(" id ", "name", "code"),
            values=(" 007 ", "北港", "001"),
        )
        source = await registry.resolve(row)

        assert registry.projection_keys(source) == (TEXT_EVIDENCE_PROJECTION_KEY,)
        evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
        assert evidence.source_type == "csv-row"
        assert evidence.source_id == "csv:%E6%9C%BA%E5%9C%BA:%20007%20"
        assert evidence.content == '{" id ":" 007 ","name":"北港","code":"001"}'
        assert evidence.metadata == {
            "document_id": "机场",
            "primary_key": " id ",
            "primary_key_value": " 007 ",
            "columns": [" id ", "name", "code"],
            "row": {" id ": " 007 ", "name": "北港", "code": "001"},
        }
        assert set(evidence.metadata) == {
            "document_id",
            "primary_key",
            "primary_key_value",
            "columns",
            "row",
        }

    asyncio.run(scenario())


def test_csv_row_projection_preserves_empty_non_primary_key_fields() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
        row = parse_csv_document(
            CSVDocumentInput(
                document_id="airports",
                primary_key="id",
                content=b"id,name,code\n001,,US\n",
            )
        )[0]

        source = await registry.resolve(row)
        evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))

        assert evidence.content == '{"id":"001","name":"","code":"US"}'
        assert evidence.metadata["row"] == {"id": "001", "name": "", "code": "US"}

    asyncio.run(scenario())

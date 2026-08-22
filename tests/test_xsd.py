"""Tests for deterministic oBDS schema parsing and lookup."""

from pathlib import Path

import pytest

from backend.xsd import (
    MAX_SOURCE_EXCERPT_LINES,
    SchemaCatalog,
    SchemaElementNotFoundError,
    SchemaError,
    SchemaVersionNotFoundError,
)

SCHEMA_DIRECTORY = Path(__file__).parents[1] / "data" / "xsd"
SCHEMA_VERSIONS = (
    "3.0.0",
    "3.0.1",
    "3.0.2",
    "3.0.3",
    "3.0.4",
    "3.0.5",
)
DIAGNOSIS_PATH = (
    "/oBDS/Menge_Patient/Patient/Menge_Meldung/Meldung/Diagnose/Diagnosesicherung"
)
PATHOLOGY_PATH = (
    "/oBDS/Menge_Patient/Patient/Menge_Meldung/Meldung/Pathologie/Diagnosesicherung"
)


@pytest.fixture(scope="module")
def catalog() -> SchemaCatalog:
    return SchemaCatalog(SCHEMA_DIRECTORY)


def test_catalog_discovers_and_sorts_versions(catalog: SchemaCatalog) -> None:
    assert catalog.versions == SCHEMA_VERSIONS
    assert catalog.latest_version == "3.0.5"


@pytest.mark.parametrize("version", SCHEMA_VERSIONS)
def test_every_bundled_schema_builds_an_index(
    catalog: SchemaCatalog,
    version: str,
) -> None:
    root = catalog.get_element(path="/oBDS", version=version)

    assert len(root) == 1
    assert root[0].version == version


def test_get_element_expands_named_types_into_xml_paths(
    catalog: SchemaCatalog,
) -> None:
    matches = catalog.get_element(path=DIAGNOSIS_PATH, version="3.0.5")

    assert len(matches) == 1
    element = matches[0]
    assert element.name == "Diagnosesicherung"
    assert element.path == DIAGNOSIS_PATH
    assert element.parent_path == DIAGNOSIS_PATH.rsplit("/", maxsplit=1)[0]
    assert element.datatype == "xs:string"
    assert element.base_datatype == "xs:string"
    assert element.min_occurs == 1
    assert element.max_occurs == 1
    assert "Höchste erreichte Diagnosesicherheit" in (element.documentation or "")
    assert element.version == "3.0.5"
    assert element.xsd_file == "oBDS_v3.0.5.xsd"
    assert element.source_url.endswith("/oBDS_v3.0.5.xsd")


def test_get_element_by_name_preserves_ambiguous_paths(
    catalog: SchemaCatalog,
) -> None:
    matches = catalog.get_element(name="diagnosesicherung", version="3.0.5")

    assert {element.path for element in matches} == {
        DIAGNOSIS_PATH,
        PATHOLOGY_PATH,
    }


def test_parent_child_relationships_are_explicit(catalog: SchemaCatalog) -> None:
    root = catalog.get_element(path="oBDS", version="3.0.5")[0]

    assert root.parent_path is None
    assert root.child_paths == (
        "/oBDS/Absender",
        "/oBDS/Meldedatum",
        "/oBDS/Menge_Patient",
        "/oBDS/Menge_Melder",
    )


def test_get_values_returns_enum_documentation(catalog: SchemaCatalog) -> None:
    result = catalog.get_values(path=DIAGNOSIS_PATH, version="3.0.5")[0]

    assert [item.value for item in result.values] == [
        "1",
        "2",
        "4",
        "5",
        "6",
        "7",
        "7.1",
        "7.2",
        "7.3",
        "8",
        "9",
    ]
    assert result.values[0].documentation == (
        "Klinisch ohne tumorspezifische Diagnostik (nur körperliche Untersuchung)"
    )


def test_get_cardinality_supports_unbounded(catalog: SchemaCatalog) -> None:
    result = catalog.get_cardinality(
        path="/oBDS/Menge_Patient/Patient",
        version="3.0.5",
    )[0]

    assert result.min_occurs == 1
    assert result.max_occurs == "unbounded"


def test_version_specific_values_do_not_leak_between_schemas(
    catalog: SchemaCatalog,
) -> None:
    old_values = catalog.get_values(path=PATHOLOGY_PATH, version="3.0.2")[0]
    new_values = catalog.get_values(path=PATHOLOGY_PATH, version="3.0.3")[0]

    assert "7.1" not in {item.value for item in old_values.values}
    assert "7.1" in {item.value for item in new_values.values}


def test_search_prefers_exact_element_name_and_defaults_to_latest(
    catalog: SchemaCatalog,
) -> None:
    matches = catalog.search("Diagnosesicherung")

    assert matches
    assert matches[0].name == "Diagnosesicherung"
    assert matches[0].version == catalog.latest_version
    assert {matches[0].path, matches[1].path} == {
        DIAGNOSIS_PATH,
        PATHOLOGY_PATH,
    }


def test_lookup_rejects_missing_selector(catalog: SchemaCatalog) -> None:
    with pytest.raises(ValueError, match="Either element name or path is required"):
        catalog.get_element(version="3.0.5")


def test_catalog_reports_unknown_version(catalog: SchemaCatalog) -> None:
    with pytest.raises(SchemaVersionNotFoundError, match="3.9.9"):
        catalog.get_element(name="oBDS", version="3.9.9")


def test_catalog_reports_missing_schema_directory(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="Schema directory not found"):
        SchemaCatalog(tmp_path / "missing")


def test_get_evidence_maps_xml_path_to_exact_xsd_declaration(
    catalog: SchemaCatalog,
) -> None:
    diagnosis = catalog.get_evidence(path=DIAGNOSIS_PATH, version="3.0.5")
    pathology = catalog.get_evidence(path=PATHOLOGY_PATH, version="3.0.5")

    assert diagnosis.element.name == pathology.element.name == "Diagnosesicherung"
    assert diagnosis.declaration_start_line == 3992
    assert pathology.declaration_start_line == 4116
    highlighted_lines = [line for line in diagnosis.source_lines if line.highlighted]
    assert highlighted_lines[0].number == 3992
    assert '<xs:element name="Diagnosesicherung">' in highlighted_lines[0].content
    assert diagnosis.declaration_truncated is False


def test_get_evidence_bounds_large_declarations(catalog: SchemaCatalog) -> None:
    evidence = catalog.get_evidence(
        path=(
            "/oBDS/Menge_Patient/Patient/Menge_Meldung/Meldung/OP/"
            "Komplikationen/Menge_Komplikation/Komplikation/Kuerzel"
        ),
        version="3.0.5",
    )

    assert len(evidence.source_lines) == MAX_SOURCE_EXCERPT_LINES
    assert evidence.declaration_truncated is True


def test_get_evidence_rejects_unknown_path(catalog: SchemaCatalog) -> None:
    with pytest.raises(SchemaElementNotFoundError, match="/oBDS/Unbekannt"):
        catalog.get_evidence(path="/oBDS/Unbekannt", version="3.0.5")

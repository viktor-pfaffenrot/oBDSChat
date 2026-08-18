"""Tests for registered model tools and their local adapters."""

import json
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, HttpUrl

from backend import tools

EXPECTED_TOOL_NAMES = (
    "search_schema",
    "get_schema_element",
    "get_schema_values",
    "get_schema_cardinality",
    "search_umsetzungsleitfaden",
    "get_source_excerpt",
)


class _Result(BaseModel):
    value: str
    url: HttpUrl


class _SchemaResult(BaseModel):
    name: str
    path: str
    version: str
    source_url: HttpUrl
    source_type: str = "xsd"


class _DocumentResult(BaseModel):
    source_id: int
    source_type: str
    title: str
    content: str
    url: HttpUrl


def _registered_tool(name: str) -> tools.LocalTool:
    return next(tool for tool in tools.TOOLS if tool.name == name)


def test_all_required_tools_are_registered_once() -> None:
    assert isinstance(tools.TOOLS, tuple)
    assert tuple(tool.name for tool in tools.TOOLS) == EXPECTED_TOOL_NAMES
    assert len({tool.name for tool in tools.TOOLS}) == len(tools.TOOLS)


def test_all_registered_tools_use_strict_chat_completion_schemas() -> None:
    for tool in tools.TOOLS:
        definition = tool.as_chat_completion_tool()
        function = definition["function"]
        properties = tool.parameters["properties"]
        required = tool.parameters["required"]

        assert function["strict"] is True
        assert function["parameters"] is tool.parameters
        assert tool.parameters["additionalProperties"] is False
        assert isinstance(properties, dict)
        assert isinstance(required, list)
        assert set(required) == set(properties)


def test_optional_arguments_are_nullable_in_strict_schemas() -> None:
    expected_nullable_properties = {
        "search_schema": {"version", "limit"},
        "get_schema_element": {"name", "path", "version"},
        "get_schema_values": {"name", "path", "version"},
        "get_schema_cardinality": {"name", "path", "version"},
        "search_umsetzungsleitfaden": {"version", "limit"},
        "get_source_excerpt": set(),
    }

    for tool in tools.TOOLS:
        properties = tool.parameters["properties"]
        assert isinstance(properties, dict)
        nullable_properties = {
            name
            for name, schema in properties.items()
            if isinstance(schema, dict)
            and isinstance(schema.get("type"), list)
            and "null" in schema["type"]
        }
        assert nullable_properties == expected_nullable_properties[tool.name]


@pytest.mark.parametrize(
    ("tool_name", "target_name", "arguments", "expected_call"),
    [
        (
            "search_schema",
            "search_schema",
            {"query": "Diagnose", "version": None, "limit": None},
            (("Diagnose",), {"version": None}),
        ),
        (
            "get_schema_element",
            "get_schema_element",
            {"name": "Diagnose", "path": None, "version": "3.0.5"},
            ((), {"name": "Diagnose", "path": None, "version": "3.0.5"}),
        ),
        (
            "get_schema_values",
            "get_schema_values",
            {"name": None, "path": "/oBDS/Diagnose", "version": "3.0.5"},
            (
                (),
                {
                    "name": None,
                    "path": "/oBDS/Diagnose",
                    "version": "3.0.5",
                },
            ),
        ),
        (
            "get_schema_cardinality",
            "get_schema_cardinality",
            {"name": "Patient", "path": None, "version": None},
            ((), {"name": "Patient", "path": None, "version": None}),
        ),
        (
            "search_umsetzungsleitfaden",
            "search_umsetzungsleitfaden",
            {"query": "Diagnose", "version": None, "limit": None},
            (("Diagnose",), {"version": None}),
        ),
    ],
)
def test_collection_tool_adapters_dispatch_and_return_json_safe_data(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    target_name: str,
    arguments: dict[str, object],
    expected_call: tuple[tuple[object, ...], dict[str, object]],
) -> None:
    result = _Result(value="belegt", url="https://example.test/source")
    handler = Mock(return_value=[result])
    monkeypatch.setattr(tools, target_name, handler)

    output = _registered_tool(tool_name).handler(**arguments)

    handler.assert_called_once_with(*expected_call[0], **expected_call[1])
    assert output == [{"value": "belegt", "url": "https://example.test/source"}]
    assert json.loads(json.dumps(output)) == output


def test_search_adapters_forward_explicit_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_search = Mock(return_value=[])
    prose_search = Mock(return_value=[])
    monkeypatch.setattr(tools, "search_schema", schema_search)
    monkeypatch.setattr(tools, "search_umsetzungsleitfaden", prose_search)

    _registered_tool("search_schema").handler(
        query="Diagnose",
        version="3.0.5",
        limit=4,
    )
    _registered_tool("search_umsetzungsleitfaden").handler(
        query="Diagnose",
        version="3.0.5",
        limit=3,
    )

    schema_search.assert_called_once_with(
        "Diagnose",
        version="3.0.5",
        limit=4,
    )
    prose_search.assert_called_once_with(
        "Diagnose",
        version="3.0.5",
        limit=3,
    )


def test_source_excerpt_adapter_returns_json_safe_data_or_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    excerpt = _DocumentResult(
        source_id=42,
        source_type="umsetzungsleitfaden",
        title="Diagnose",
        content="Vollständiger Beleg.",
        url="https://example.test/source",
    )
    source_lookup = Mock(
        side_effect=[
            excerpt,
            None,
        ]
    )
    monkeypatch.setattr(tools, "get_source_excerpt", source_lookup)
    handler = _registered_tool("get_source_excerpt").handler

    result = handler(source_id=42)
    missing_result = handler(source_id=404)

    assert result == {
        "source_id": 42,
        "source_type": "umsetzungsleitfaden",
        "title": "Diagnose",
        "content": "Vollständiger Beleg.",
        "url": "https://example.test/source",
        "citation_id": "umsetzungsleitfaden:42",
    }
    assert missing_result is None
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


def test_schema_tool_results_include_stable_citation_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_result = _SchemaResult(
        name="Diagnosesicherung",
        path="/oBDS/Diagnose/Diagnosesicherung",
        version="3.0.5",
        source_url="https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd",
    )
    monkeypatch.setattr(tools, "search_schema", Mock(return_value=[schema_result]))

    result = _registered_tool("search_schema").handler(
        query="Diagnosesicherung",
        version="3.0.5",
        limit=None,
    )

    assert isinstance(result, list)
    assert result[0]["citation_id"] == ("xsd:3.0.5:/oBDS/Diagnose/Diagnosesicherung")

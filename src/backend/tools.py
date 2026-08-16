"""OpenAI function-tool registration for local oBDS data sources."""

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel

from backend.llm import LocalTool
from backend.search import get_source_excerpt, search_umsetzungsleitfaden
from backend.xsd import (
    get_schema_cardinality,
    get_schema_element,
    get_schema_values,
    search_schema,
)

CITATION_ID_FIELD: Final = "citation_id"


def _object_schema(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _nullable_string(description: str) -> dict[str, object]:
    return {
        "type": ["string", "null"],
        "description": description,
    }


def _version_property() -> dict[str, object]:
    return _nullable_string(
        "Exact oBDS schema version, for example 3.0.5. Use null for latest."
    )


def _limit_property(default: int) -> dict[str, object]:
    return {
        "type": ["integer", "null"],
        "minimum": 1,
        "description": f"Maximum results. Use null for the default of {default}.",
    }


def _schema_selector_properties() -> dict[str, object]:
    return {
        "name": _nullable_string(
            "Exact XML element name. Use null when selecting by path."
        ),
        "path": _nullable_string(
            "Exact XML path. Use null when selecting only by element name."
        ),
        "version": _version_property(),
    }


def _dump_models(models: Sequence[BaseModel]) -> list[dict[str, object]]:
    return [_dump_model(model) for model in models]


def _dump_optional_model(model: BaseModel | None) -> dict[str, object] | None:
    if model is None:
        return None
    return _dump_model(model)


def _dump_model(model: BaseModel) -> dict[str, object]:
    data = model.model_dump(mode="json")
    citation_id = _citation_id(data)
    if citation_id is not None:
        data[CITATION_ID_FIELD] = citation_id
    return data


def _citation_id(data: dict[str, object]) -> str | None:
    source_type = data.get("source_type")
    if source_type == "xsd":
        version = data.get("version")
        path = data.get("path")
        if isinstance(version, str) and isinstance(path, str):
            return f"xsd:{version}:{path}"

    source_id = data.get("source_id")
    if isinstance(source_type, str) and isinstance(source_id, int):
        return f"{source_type}:{source_id}"
    return None


def _search_schema(
    query: str,
    version: str | None,
    limit: int | None,
) -> list[dict[str, object]]:
    if limit is None:
        return _dump_models(search_schema(query, version=version))
    return _dump_models(search_schema(query, version=version, limit=limit))


def _get_schema_element(
    name: str | None,
    path: str | None,
    version: str | None,
) -> list[dict[str, object]]:
    return _dump_models(get_schema_element(name=name, path=path, version=version))


def _get_schema_values(
    name: str | None,
    path: str | None,
    version: str | None,
) -> list[dict[str, object]]:
    return _dump_models(get_schema_values(name=name, path=path, version=version))


def _get_schema_cardinality(
    name: str | None,
    path: str | None,
    version: str | None,
) -> list[dict[str, object]]:
    return _dump_models(get_schema_cardinality(name=name, path=path, version=version))


def _search_umsetzungsleitfaden(
    query: str,
    version: str | None,
    limit: int | None,
) -> list[dict[str, object]]:
    if limit is None:
        return _dump_models(search_umsetzungsleitfaden(query, version=version))
    return _dump_models(search_umsetzungsleitfaden(query, version=version, limit=limit))


def _get_source_excerpt(source_id: int) -> dict[str, object] | None:
    return _dump_optional_model(get_source_excerpt(source_id))


TOOLS: Final[tuple[LocalTool, ...]] = (
    LocalTool(
        name="search_schema",
        description=(
            "Search deterministic oBDS XSD facts when an exact element name or "
            "XML path is not yet known. Returns matching structure, datatype, "
            "values, documentation, relationships, version, and source metadata."
        ),
        parameters=_object_schema(
            {
                "query": {
                    "type": "string",
                    "description": (
                        "Element name, XML path fragment, datatype, value, or "
                        "documentation text to find."
                    ),
                },
                "version": _version_property(),
                "limit": _limit_property(10),
            }
        ),
        handler=_search_schema,
    ),
    LocalTool(
        name="get_schema_element",
        description=(
            "Get exact oBDS XSD element facts, including XML path, datatype, "
            "documentation, parent, children, cardinality, and source metadata. "
            "Name lookups may return multiple paths."
        ),
        parameters=_object_schema(_schema_selector_properties()),
        handler=_get_schema_element,
    ),
    LocalTool(
        name="get_schema_values",
        description=(
            "Get allowed enumeration values and their documentation for an exact "
            "oBDS XSD element name or XML path."
        ),
        parameters=_object_schema(_schema_selector_properties()),
        handler=_get_schema_values,
    ),
    LocalTool(
        name="get_schema_cardinality",
        description=(
            "Get minOccurs and maxOccurs for an exact oBDS XSD element name or "
            "XML path."
        ),
        parameters=_object_schema(_schema_selector_properties()),
        handler=_get_schema_cardinality,
    ),
    LocalTool(
        name="search_umsetzungsleitfaden",
        description=(
            "Search official Umsetzungsleitfaden prose for field meaning, "
            "implementation guidance, rules, and edge cases. Returns ranked "
            "excerpts with source IDs and citation metadata."
        ),
        parameters=_object_schema(
            {
                "query": {
                    "type": "string",
                    "description": "German prose search query.",
                },
                "version": _nullable_string(
                    "Exact oBDS version. Use null for version-independent results."
                ),
                "limit": _limit_property(5),
            }
        ),
        handler=_search_umsetzungsleitfaden,
    ),
    LocalTool(
        name="get_source_excerpt",
        description=(
            "Get complete stored source content and citation metadata for a source "
            "ID returned by search_umsetzungsleitfaden."
        ),
        parameters=_object_schema(
            {
                "source_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Source ID returned by prose search.",
                }
            }
        ),
        handler=_get_source_excerpt,
    ),
)

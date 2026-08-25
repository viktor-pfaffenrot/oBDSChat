"""Deterministic lookup over versioned oBDS XML schemas."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any, Final, Literal

import xmlschema
from lxml import etree  # ty: ignore[unresolved-import]
from pydantic import BaseModel, ConfigDict, Field

from backend.config import load_settings

XSD_NAMESPACE: Final = "http://www.w3.org/2001/XMLSchema"
ENUMERATION_FACET: Final = f"{{{XSD_NAMESPACE}}}enumeration"
OFFICIAL_XSD_BASE_URL: Final = "https://www.basisdatensatz.de/xml"
VERSION_PATTERN: Final = re.compile(r"^\d+\.\d+\.\d+$")
SOURCE_CONTEXT_LINES: Final = 3
MAX_SOURCE_EXCERPT_LINES: Final = 160

MaxOccurs = int | Literal["unbounded"]


class SchemaError(RuntimeError):
    """Base error for schema discovery and parsing failures."""


class SchemaVersionNotFoundError(SchemaError):
    """Raised when a requested oBDS schema version is unavailable."""


class SchemaElementNotFoundError(SchemaError):
    """Raised when an exact XML path does not exist in a schema version."""


class SchemaEnumValue(BaseModel):
    """One allowed XSD enumeration value and its optional documentation."""

    model_config = ConfigDict(frozen=True)

    value: str = Field(description="Allowed lexical value from the XSD enumeration.")
    documentation: str | None = Field(
        default=None,
        description="Documentation attached to this enumeration value.",
    )


class SchemaElement(BaseModel):
    """Source-grounded facts for one element occurrence in an oBDS schema."""

    model_config = ConfigDict(frozen=True)

    name: str
    path: str
    datatype: str
    base_datatype: str | None = None
    min_occurs: int = Field(ge=0)
    max_occurs: MaxOccurs
    allowed_values: tuple[SchemaEnumValue, ...] = ()
    documentation: str | None = None
    datatype_documentation: str | None = None
    parent_path: str | None = None
    child_paths: tuple[str, ...] = ()
    version: str
    xsd_file: str
    source_url: str
    source_type: Literal["xsd"] = "xsd"


class SchemaValues(BaseModel):
    """Allowed values for one matched schema element."""

    model_config = ConfigDict(frozen=True)

    name: str
    path: str
    values: tuple[SchemaEnumValue, ...]
    version: str
    xsd_file: str
    source_url: str
    source_type: Literal["xsd"] = "xsd"


class SchemaCardinality(BaseModel):
    """Cardinality for one matched schema element."""

    model_config = ConfigDict(frozen=True)

    name: str
    path: str
    min_occurs: int = Field(ge=0)
    max_occurs: MaxOccurs
    version: str
    xsd_file: str
    source_url: str
    source_type: Literal["xsd"] = "xsd"


class SchemaSourceLine(BaseModel):
    """One numbered XSD source line and its evidence-highlight state."""

    model_config = ConfigDict(frozen=True)

    number: int = Field(gt=0, description="One-based source line number.")
    content: str = Field(description="Original XSD source line content.")
    highlighted: bool = Field(
        description="Whether this line belongs to the selected declaration."
    )


class SchemaEvidence(BaseModel):
    """Exact schema facts with a bounded excerpt of their source declaration."""

    model_config = ConfigDict(frozen=True)

    element: SchemaElement
    source_lines: tuple[SchemaSourceLine, ...]
    declaration_start_line: int | None = Field(default=None, gt=0)
    declaration_end_line: int | None = Field(default=None, gt=0)
    declaration_truncated: bool = False


class _SourceLocation(BaseModel):
    """Line range for one element declaration in the original XSD."""

    model_config = ConfigDict(frozen=True)

    start_line: int = Field(gt=0)
    end_line: int = Field(gt=0)


class _SchemaIndex(BaseModel):
    """Parsed element indexes for one schema version."""

    model_config = ConfigDict(frozen=True)

    elements: tuple[SchemaElement, ...]
    by_name: dict[str, tuple[SchemaElement, ...]]
    by_path: dict[str, SchemaElement]
    source_lines: tuple[str, ...]
    source_locations: dict[str, _SourceLocation]


class SchemaCatalog:
    """Lazily parse and index oBDS schemas stored by version."""

    def __init__(self, schema_directory: Path) -> None:
        self.schema_directory = schema_directory.expanduser().resolve()
        self._versions = _discover_versions(self.schema_directory)
        self._indexes: dict[str, _SchemaIndex] = {}
        self._lock = RLock()

    @property
    def versions(self) -> tuple[str, ...]:
        """Return available versions from oldest to newest."""
        return self._versions

    @property
    def latest_version(self) -> str:
        """Return the newest available semantic version."""
        return self._versions[-1]

    def search(
        self,
        query: str,
        version: str | None = None,
        limit: int = 10,
    ) -> list[SchemaElement]:
        """Search element names, paths, datatypes, values, and documentation."""
        normalized_query = _normalize_search_text(query)
        if not normalized_query:
            raise ValueError("Schema search query must not be empty")
        if limit < 1:
            raise ValueError("Schema search limit must be at least 1")

        index = self._get_index(version)
        matches = [
            (_search_score(element, normalized_query), element)
            for element in index.elements
        ]
        ranked_matches = [match for match in matches if match[0] > 0]
        ranked_matches.sort(key=lambda match: (-match[0], match[1].path))
        return [element for _, element in ranked_matches[:limit]]

    def get_element(
        self,
        *,
        name: str | None = None,
        path: str | None = None,
        version: str | None = None,
    ) -> list[SchemaElement]:
        """Find elements by exact name, path, or both."""
        normalized_name = _normalize_lookup_value(name, "Element name")
        normalized_path = _normalize_lookup_value(path, "Element path")
        if normalized_name is None and normalized_path is None:
            raise ValueError("Either element name or path is required")

        index = self._get_index(version)
        if normalized_path is not None:
            lookup_path = _canonicalize_path(normalized_path)
            element = index.by_path.get(lookup_path.casefold())
            candidates = [] if element is None else [element]
        else:
            assert normalized_name is not None
            candidates = list(index.by_name.get(normalized_name.casefold(), ()))

        if normalized_name is None:
            return candidates
        return [
            element
            for element in candidates
            if element.name.casefold() == normalized_name.casefold()
        ]

    def get_values(
        self,
        *,
        name: str | None = None,
        path: str | None = None,
        version: str | None = None,
    ) -> list[SchemaValues]:
        """Return allowed enumeration values for matching elements."""
        return [
            SchemaValues(
                name=element.name,
                path=element.path,
                values=element.allowed_values,
                version=element.version,
                xsd_file=element.xsd_file,
                source_url=element.source_url,
            )
            for element in self.get_element(name=name, path=path, version=version)
        ]

    def get_cardinality(
        self,
        *,
        name: str | None = None,
        path: str | None = None,
        version: str | None = None,
    ) -> list[SchemaCardinality]:
        """Return cardinality for matching elements."""
        return [
            SchemaCardinality(
                name=element.name,
                path=element.path,
                min_occurs=element.min_occurs,
                max_occurs=element.max_occurs,
                version=element.version,
                xsd_file=element.xsd_file,
                source_url=element.source_url,
            )
            for element in self.get_element(name=name, path=path, version=version)
        ]

    def get_evidence(self, *, path: str, version: str) -> SchemaEvidence:
        """Return facts and original XSD lines for one exact XML path."""
        normalized_path = _normalize_lookup_value(path, "Element path")
        assert normalized_path is not None
        canonical_path = _canonicalize_path(normalized_path)
        index = self._get_index(version)
        path_key = canonical_path.casefold()
        element = index.by_path.get(path_key)
        if element is None:
            raise SchemaElementNotFoundError(
                f"Element path {canonical_path!r} is unavailable in oBDS {version}"
            )

        location = index.source_locations.get(path_key)
        if location is None:
            return SchemaEvidence(element=element, source_lines=())

        excerpt_start = max(1, location.start_line - SOURCE_CONTEXT_LINES)
        requested_end = min(
            len(index.source_lines),
            location.end_line + SOURCE_CONTEXT_LINES,
        )
        excerpt_end = min(
            requested_end,
            excerpt_start + MAX_SOURCE_EXCERPT_LINES - 1,
        )
        lines = tuple(
            SchemaSourceLine(
                number=line_number,
                content=index.source_lines[line_number - 1],
                highlighted=location.start_line <= line_number <= location.end_line,
            )
            for line_number in range(excerpt_start, excerpt_end + 1)
        )
        return SchemaEvidence(
            element=element,
            source_lines=lines,
            declaration_start_line=location.start_line,
            declaration_end_line=location.end_line,
            declaration_truncated=excerpt_end < location.end_line,
        )

    def resolve_version(self, version: str | None) -> str:
        """Resolve an optional version, defaulting to the newest schema."""
        if version is None:
            return self.latest_version

        normalized_version = version.strip()
        if normalized_version not in self._versions:
            available = ", ".join(self._versions)
            raise SchemaVersionNotFoundError(
                f"oBDS schema version {version!r} is unavailable. "
                f"Available versions: {available}"
            )
        return normalized_version

    def _get_index(self, version: str | None) -> _SchemaIndex:
        resolved_version = self.resolve_version(version)
        with self._lock:
            cached_index = self._indexes.get(resolved_version)
            if cached_index is not None:
                return cached_index

            schema_path = (
                self.schema_directory
                / resolved_version
                / f"oBDS_v{resolved_version}.xsd"
            )
            if not schema_path.is_file():
                raise SchemaError(f"Schema file not found: {schema_path}")

            index = _build_index(schema_path, resolved_version)
            self._indexes[resolved_version] = index
            return index


def search_schema(
    query: str,
    version: str | None = None,
    limit: int = 10,
) -> list[SchemaElement]:
    """Search the configured oBDS schema, using the latest version by default."""
    return get_schema_catalog().search(query, version=version, limit=limit)


def get_schema_element(
    name: str | None = None,
    path: str | None = None,
    version: str | None = None,
) -> list[SchemaElement]:
    """Find configured schema elements by exact name, path, or both."""
    return get_schema_catalog().get_element(name=name, path=path, version=version)


def get_schema_values(
    name: str | None = None,
    path: str | None = None,
    version: str | None = None,
) -> list[SchemaValues]:
    """Return configured schema enumeration values by element name or path."""
    return get_schema_catalog().get_values(name=name, path=path, version=version)


def get_schema_cardinality(
    name: str | None = None,
    path: str | None = None,
    version: str | None = None,
) -> list[SchemaCardinality]:
    """Return configured schema cardinality by element name or path."""
    return get_schema_catalog().get_cardinality(
        name=name,
        path=path,
        version=version,
    )


def get_schema_evidence(path: str, version: str) -> SchemaEvidence:
    """Return exact facts and source lines for one configured schema element."""
    return get_schema_catalog().get_evidence(path=path, version=version)


@lru_cache(maxsize=1)
def get_schema_catalog() -> SchemaCatalog:
    """Return the process-wide schema catalog."""
    schema_directory = load_settings().base_dir / "data" / "xsd"
    return SchemaCatalog(schema_directory)


def clear_schema_cache() -> None:
    """Clear cached schemas after source synchronization or in tests."""
    get_schema_catalog.cache_clear()


def _discover_versions(schema_directory: Path) -> tuple[str, ...]:
    if not schema_directory.is_dir():
        raise SchemaError(f"Schema directory not found: {schema_directory}")

    versions = [
        directory.name
        for directory in schema_directory.iterdir()
        if directory.is_dir()
        and VERSION_PATTERN.fullmatch(directory.name)
        and (directory / f"oBDS_v{directory.name}.xsd").is_file()
    ]
    if not versions:
        raise SchemaError(f"No versioned oBDS schemas found in {schema_directory}")
    return tuple(sorted(versions, key=_version_key))


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _build_index(schema_path: Path, version: str) -> _SchemaIndex:
    try:
        schema = xmlschema.XMLSchema(schema_path)
    except (OSError, xmlschema.XMLSchemaException) as error:
        raise SchemaError(f"Failed to parse schema: {schema_path}") from error

    declared_version = (schema.version or "").split(maxsplit=1)[0]
    if declared_version != version:
        raise SchemaError(
            f"Schema {schema_path} declares version {schema.version!r}, "
            f"expected {version!r}"
        )

    source_lines, source_positions, parsed_source_root = _load_source_map(
        schema_path,
        schema.source.root,
    )
    elements: list[SchemaElement] = []
    source_locations: dict[str, _SourceLocation] = {}
    source_url = f"{OFFICIAL_XSD_BASE_URL}/{schema_path.name}"
    for root_element in schema.elements.values():
        _collect_element(
            element=root_element,
            path=f"/{root_element.local_name}",
            parent_path=None,
            version=version,
            xsd_file=schema_path.name,
            source_url=source_url,
            source_positions=source_positions,
            parsed_source_root=parsed_source_root,
            type_stack=frozenset(),
            output=elements,
            source_locations=source_locations,
        )

    by_name: defaultdict[str, list[SchemaElement]] = defaultdict(list)
    by_path: dict[str, SchemaElement] = {}
    for element in elements:
        by_name[element.name.casefold()].append(element)
        path_key = element.path.casefold()
        if path_key in by_path:
            raise SchemaError(
                f"Duplicate element path in {schema_path}: {element.path}"
            )
        by_path[path_key] = element

    return _SchemaIndex(
        elements=tuple(elements),
        by_name={name: tuple(matches) for name, matches in by_name.items()},
        by_path=by_path,
        source_lines=source_lines,
        source_locations=source_locations,
    )


def _collect_element(
    *,
    element: Any,
    path: str,
    parent_path: str | None,
    version: str,
    xsd_file: str,
    source_url: str,
    source_positions: dict[int, tuple[int, ...]],
    parsed_source_root: Any,
    type_stack: frozenset[int],
    output: list[SchemaElement],
    source_locations: dict[str, _SourceLocation],
) -> None:
    xsd_type = element.type
    model_group = getattr(xsd_type, "model_group", None)
    type_identity = id(xsd_type)
    can_descend = model_group is not None and type_identity not in type_stack
    child_elements = list(model_group.iter_elements()) if can_descend else []
    child_paths = tuple(f"{path}/{child.local_name}" for child in child_elements)

    schema_element = SchemaElement(
        name=element.local_name,
        path=path,
        datatype=_datatype_name(xsd_type),
        base_datatype=_base_datatype_name(xsd_type),
        min_occurs=element.min_occurs,
        max_occurs=("unbounded" if element.max_occurs is None else element.max_occurs),
        allowed_values=_enum_values(xsd_type),
        documentation=_annotation_text(element.annotation),
        datatype_documentation=_annotation_text(xsd_type.annotation),
        parent_path=parent_path,
        child_paths=child_paths,
        version=version,
        xsd_file=xsd_file,
        source_url=source_url,
    )
    output.append(schema_element)
    source_location = _locate_source_element(
        element.elem,
        source_positions,
        parsed_source_root,
    )
    if source_location is not None:
        source_locations[schema_element.path.casefold()] = source_location

    next_type_stack = type_stack | {type_identity}
    for child, child_path in zip(child_elements, child_paths, strict=True):
        _collect_element(
            element=child,
            path=child_path,
            parent_path=path,
            version=version,
            xsd_file=xsd_file,
            source_url=source_url,
            source_positions=source_positions,
            parsed_source_root=parsed_source_root,
            type_stack=next_type_stack,
            output=output,
            source_locations=source_locations,
        )


def _load_source_map(
    schema_path: Path,
    schema_root: Any,
) -> tuple[tuple[str, ...], dict[int, tuple[int, ...]], Any]:
    try:
        source_text = schema_path.read_text(encoding="utf-8")
        parser = etree.XMLParser(
            no_network=True,
            remove_comments=True,
            remove_pis=True,
            resolve_entities=False,
        )
        parsed_source_root = etree.parse(str(schema_path), parser).getroot()
    except (OSError, UnicodeError, etree.XMLSyntaxError) as error:
        raise SchemaError(f"Failed to read schema source: {schema_path}") from error

    positions: dict[int, tuple[int, ...]] = {}

    def collect_positions(node: Any, position: tuple[int, ...]) -> None:
        positions[id(node)] = position
        for child_index, child in enumerate(node):
            collect_positions(child, (*position, child_index))

    collect_positions(schema_root, ())
    return tuple(source_text.splitlines()), positions, parsed_source_root


def _locate_source_element(
    schema_element: Any,
    source_positions: dict[int, tuple[int, ...]],
    parsed_source_root: Any,
) -> _SourceLocation | None:
    position = source_positions.get(id(schema_element))
    if position is None:
        return None

    source_element = parsed_source_root
    try:
        for child_index in position:
            source_element = source_element[child_index]
    except IndexError:
        return None

    start_line = source_element.sourceline
    if not isinstance(start_line, int):
        return None
    serialized_element = etree.tostring(
        source_element,
        encoding="unicode",
        with_tail=False,
    )
    return _SourceLocation(
        start_line=start_line,
        end_line=start_line + serialized_element.count("\n"),
    )


def _datatype_name(xsd_type: Any) -> str:
    local_name = xsd_type.local_name
    if local_name is not None:
        if xsd_type.target_namespace == XSD_NAMESPACE:
            return f"xs:{local_name}"
        return local_name

    primitive_type = getattr(xsd_type, "primitive_type", None)
    if primitive_type is not None:
        return primitive_type.prefixed_name
    return "complexType"


def _base_datatype_name(xsd_type: Any) -> str | None:
    primitive_type = getattr(xsd_type, "primitive_type", None)
    if primitive_type is None:
        return None
    return primitive_type.prefixed_name


def _enum_values(xsd_type: Any) -> tuple[SchemaEnumValue, ...]:
    current_type = xsd_type
    visited_types: set[int] = set()
    while current_type is not None and id(current_type) not in visited_types:
        visited_types.add(id(current_type))
        facets = getattr(current_type, "facets", {})
        facet = facets.get(ENUMERATION_FACET)
        if facet is not None:
            return tuple(
                SchemaEnumValue(
                    value=facet_element.attrib["value"],
                    documentation=_xml_documentation(facet_element),
                )
                for facet_element in facet
            )
        current_type = getattr(current_type, "base_type", None)
    return ()


def _annotation_text(annotation: Any | None) -> str | None:
    if annotation is None:
        return None
    documents = [_normalized_xml_text(element) for element in annotation.documentation]
    return "\n".join(document for document in documents if document) or None


def _xml_documentation(element: Any) -> str | None:
    documentation_elements = element.findall(f".//{{{XSD_NAMESPACE}}}documentation")
    documents = [_normalized_xml_text(item) for item in documentation_elements]
    return "\n".join(document for document in documents if document) or None


def _normalized_xml_text(element: Any) -> str:
    return " ".join("".join(element.itertext()).split())


def _normalize_lookup_value(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{label} must not be empty")
    return normalized_value


def _canonicalize_path(path: str) -> str:
    canonical_path = "/" + path.strip("/")
    if canonical_path == "/":
        raise ValueError("Element path must identify an element")
    return canonical_path


def _normalize_search_text(value: str) -> str:
    normalized_value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", normalized_value).split())


def _search_score(element: SchemaElement, normalized_query: str) -> int:
    normalized_name = _normalize_search_text(element.name)
    normalized_path = _normalize_search_text(element.path)
    searchable_parts = [
        normalized_name,
        normalized_path,
        _normalize_search_text(element.datatype),
        _normalize_search_text(element.base_datatype or ""),
        _normalize_search_text(element.documentation or ""),
        _normalize_search_text(element.datatype_documentation or ""),
    ]
    searchable_parts.extend(
        _normalize_search_text(f"{value.value} {value.documentation or ''}")
        for value in element.allowed_values
    )
    searchable_text = " ".join(searchable_parts)

    compact_query = normalized_query.replace(" ", "")
    compact_name = normalized_name.replace(" ", "")
    score = 0
    if normalized_query == normalized_name:
        score += 1_000
    elif compact_query == compact_name:
        score += 900
    elif compact_query in compact_name:
        score += 700
    if normalized_query in normalized_path:
        score += 300
    if normalized_query in searchable_text:
        score += 150

    query_terms = normalized_query.split()
    matched_terms = sum(term in searchable_text for term in query_terms)
    score += matched_terms * 10
    if query_terms and matched_terms == len(query_terms):
        score += 50
    return score

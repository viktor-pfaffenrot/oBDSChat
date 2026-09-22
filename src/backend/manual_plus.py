"""Manual Plus storage-HTML extraction; no PDF processing or publication."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Literal
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from pydantic import BaseModel, ConfigDict, Field

from backend.evidence import EvidenceBlock, HtmlLocator, SourceEvidence

EXTRACTION_VERSION = "manual-plus-html-v1"
REGISTRIES = {
    "BW": "Baden-Württemberg",
    "BY": "Bayern",
    "BB": "Brandenburg",
    "HB": "Bremen",
    "HH": "Hamburg",
    "HE": "Hessen",
    "MV": "Mecklenburg-Vorpommern",
    "NI": "Niedersachsen",
    "NW": "Nordrhein-Westfalen",
    "RP": "Rheinland-Pfalz",
    "SL": "Saarland",
    "SN": "Sachsen",
    "ST": "Sachsen-Anhalt",
    "SH": "Schleswig-Holstein",
    "TH": "Thüringen",
}
RegistryStatus = Literal["positive", "qualified", "negative", "unreviewed", "unknown"]
PALETTE: dict[str, RegistryStatus] = {
    "#abf5d1": "positive",
    "#fff0b3": "qualified",
    "#ffbdad": "negative",
    "#f4f5f7": "unreviewed",
}
_HEADINGS = tuple(f"h{i}" for i in range(1, 7))
_IGNORED = {"ac:parameter", "ac:adf-attribute", "ac:adf-fallback", "script", "style"}


class SemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColorAnnotation(SemanticModel):
    path: str
    text: str
    attribute: str
    observed: str
    normalized: str | None
    meaning: Literal["change", "uninterpreted"]


class TableCell(SemanticModel):
    path: str
    row: int
    column: int
    rowspan: int = Field(ge=1)
    colspan: int = Field(ge=1)
    header: bool
    text: str
    headers: tuple[str, ...] = ()
    struck: bool = False


class EvidenceLink(SemanticModel):
    path: str
    text: str
    url: str


class StructuredTable(SemanticModel):
    cells: tuple[TableCell, ...]
    # Each slot references its originating cell, including spanning cells.
    grid: tuple[tuple[str | None, ...], ...]
    links: tuple[EvidenceLink, ...] = ()


class Qualification(SemanticModel):
    registry: str
    original_label: str
    evidence_keys: tuple[str, ...]
    path: str
    mentioned_registries: tuple[str, ...] = ()


class RegistryAssessment(SemanticModel):
    registry: str
    original_label: str
    status: RegistryStatus
    evidence_key: str
    path: str
    observed_colors: tuple[ColorAnnotation, ...]
    qualification_keys: tuple[str, ...] = ()
    struck: bool = False


class SourceConflict(SemanticModel):
    kind: Literal["registry_label_mismatch", "multiple_assessments"]
    registries: tuple[str, ...]
    evidence_keys: tuple[str, ...]
    description: str


class PendingReference(SemanticModel):
    path: str
    kind: Literal["attachment", "macro", "image"]
    name: str
    attributes: dict[str, str]


class ManualPlusContext(SemanticModel):
    extraction_version: str = EXTRACTION_VERSION
    validation: Literal["present", "absent", "unreadable"]
    assessments: tuple[RegistryAssessment, ...] = ()
    qualifications: tuple[Qualification, ...] = ()
    annotations: tuple[ColorAnnotation, ...] = ()
    conflicts: tuple[SourceConflict, ...] = ()
    pending_references: tuple[PendingReference, ...] = ()
    extraction_issues: tuple[str, ...] = ()
    html_semantics_complete: bool
    # A page-only import never establishes coverage of required decision PDFs.
    corpus_complete: Literal[False] = False
    pdf_coverage: Literal["pending"] = "pending"


class SemanticExtractionError(ValueError):
    """An incomplete candidate, including the original and diagnostic evidence."""

    def __init__(self, candidate: SourceEvidence, issues: list[str]) -> None:
        self.candidate = candidate
        super().__init__(f"Page {candidate.identity.native_id}: " + "; ".join(issues))


def manual_plus_context(source: SourceEvidence) -> ManualPlusContext:
    """Validate page context after storage, without inferring PDF applicability."""
    return ManualPlusContext.model_validate(source.metadata["manual_plus"])


def _text(tag: Tag) -> str:
    strings = (
        value
        for value in tag.descendants
        if isinstance(value, NavigableString)
        and not isinstance(value, Comment)
        and not any(
            parent is not tag and parent.name in _IGNORED for parent in value.parents
        )
    )
    return " ".join(" ".join(strings).split())


def _path(tag: Tag) -> str:
    parts: list[str] = []
    node: Tag | None = tag
    while node is not None and node.name != "[document]":
        index = 1 + len(list(node.find_previous_siblings(node.name)))
        parts.append(f"{node.name}[{index}]")
        node = node.parent
    return "/" + "/".join(reversed(parts))


def _normalize_color(value: str) -> str | None:
    value = value.strip().lower().removesuffix("!important").strip()
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return value
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        return "#" + "".join(char * 2 for char in value[1:])
    match = re.fullmatch(
        r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*1(?:\.0+)?)?\s*\)", value
    )
    if match and all(int(part) <= 255 for part in match.groups()):
        return "#" + "".join(f"{int(part):02x}" for part in match.groups())
    return None


def _colors(tag: Tag) -> list[ColorAnnotation]:
    values: list[tuple[str, str]] = []
    for name in ("data-highlight-colour", "data-highlight-color", "bgcolor"):
        value = tag.get(name)
        if isinstance(value, str):
            values.append((name, value))
    for declaration in str(tag.get("style", "")).split(";"):
        name, separator, value = declaration.partition(":")
        if separator and name.strip().lower() in {"background", "background-color"}:
            values.append((name.strip().lower(), value.strip()))
    result = []
    for name, value in values:
        normalized = _normalize_color(value)
        result.append(
            ColorAnnotation(
                path=_path(tag),
                text=_text(tag),
                attribute=name,
                observed=value,
                normalized=normalized,
                meaning="change" if normalized == "#c6edfb" else "uninterpreted",
            )
        )
    return result


def _table(table: Tag) -> StructuredTable:
    cells: list[TableCell] = []
    occupied: dict[tuple[int, int], str] = {}
    rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while (row_index, column) in occupied:
                column += 1
            try:
                rowspan = int(str(cell.get("rowspan", "1")))
                colspan = int(str(cell.get("colspan", "1")))
            except ValueError as error:
                raise ValueError(f"Invalid table span at {_path(cell)}") from error
            if not 1 <= rowspan <= len(rows) - row_index or not 1 <= colspan <= 1000:
                raise ValueError(f"Unsupported table span at {_path(cell)}")
            path = _path(cell)
            for r in range(row_index, row_index + rowspan):
                for c in range(column, column + colspan):
                    if (r, c) in occupied:
                        raise ValueError(f"Overlapping table cells at {path}")
                    occupied[r, c] = path
            cells.append(
                TableCell(
                    path=path,
                    row=row_index,
                    column=column,
                    rowspan=rowspan,
                    colspan=colspan,
                    header=cell.name == "th",
                    text=_text(cell),
                    struck=cell.find(["del", "s", "strike"]) is not None,
                )
            )
            column += colspan
    width = max((c + 1 for _, c in occupied), default=0)
    associated = []
    for cell in cells:
        headers = tuple(
            h.path
            for h in cells
            if h.header
            and h.path != cell.path
            and (
                (
                    h.row < cell.row
                    and h.column < cell.column + cell.colspan
                    and cell.column < h.column + h.colspan
                )
                or (h.column < cell.column and h.row <= cell.row < h.row + h.rowspan)
            )
        )
        associated.append(cell.model_copy(update={"headers": headers}))
    return StructuredTable(
        cells=tuple(associated),
        grid=tuple(
            tuple(occupied.get((r, c)) for c in range(width)) for r in range(len(rows))
        ),
    )


def _blocks(container: Tag) -> Iterator[Tag | NavigableString]:
    for child in container.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            if str(child).strip():
                yield child
            continue
        if not isinstance(child, Tag) or child.name in _IGNORED:
            continue
        if child.name in (*_HEADINGS, "table", "p", "pre", "blockquote"):
            yield child
            if child.name == "table":
                yield from child.find_all("table")
        else:
            yield from _blocks(child)


def _macro_title(tag: Tag) -> str:
    title = tag.find("ac:parameter", attrs={"ac:name": "title"}, recursive=False)
    return _text(title) if title else ""


def _is_example(table: Tag) -> bool:
    previous = table.find_previous_sibling()
    return previous is not None and _text(previous).casefold().rstrip(":") == "beispiel"


def _pending(soup: Tag) -> list[PendingReference]:
    result = []
    for tag in soup.find_all(
        [
            "ri:attachment",
            "ac:image",
            "img",
            "svg",
            "ac:structured-macro",
            "ac:adf-node",
        ]
    ):
        if tag.find_parent("ac:adf-fallback") is not None:
            continue
        kind: Literal["attachment", "macro", "image"] = "macro"
        name = str(tag.get("ac:name", tag.name))
        if tag.name == "ri:attachment":
            kind = "attachment"
            name = str(tag.get("ri:filename", ""))
        elif tag.name in {"ac:image", "img", "svg"}:
            kind = "image"
        elif tag.name == "ac:adf-node":
            name = str(tag.get("type", ""))
            if name in {"panel", "decision-list", "decision-item"}:
                continue
        elif name in {
            "expand",
            "toc",
            "anchor",
            "children",
            "info",
            "note",
            "tip",
            "warning",
            "panel",
            "recently-updated",
        }:
            continue
        attributes = {key: str(value) for key, value in tag.attrs.items()}
        for parameter in tag.find_all("ac:parameter", recursive=False):
            attributes[str(parameter.get("ac:name", ""))] = _text(parameter)
        result.append(
            PendingReference(
                path=_path(tag), kind=kind, name=name, attributes=attributes
            )
        )
    return result


def extract_manual_plus_page(source: SourceEvidence) -> SourceEvidence:
    """Extract one original HTML page, rejecting incomplete registry semantics.

    Context references block local keys within this source's current hash/revision.
    Unknown status stays unknown in the diagnostic candidate; it never means gray.
    """
    if (
        source.identity.source_family != "manual_plus"
        or source.original.media_type != "text/html"
        or source.identity.native_kind != "page"
    ):
        raise ValueError("Manual Plus extraction requires an original HTML page")
    soup = BeautifulSoup(source.original.content.decode("utf-8"), "html.parser")
    # ADF fallback duplicates the primary content. Keep original bytes unchanged.
    for fallback in soup.find_all("ac:adf-fallback"):
        fallback.decompose()
    issues: list[str] = []
    blocks, tables = _extract_blocks(soup, str(source.url), issues)
    qualifications = _qualifications(soup, blocks, issues)
    assessments = _assessments(tables, qualifications, issues)
    conflicts = _conflicts(assessments, qualifications)
    annotations = tuple(color for tag in soup.find_all(True) for color in _colors(tag))
    pending = _pending(soup)
    for reference in pending:
        if reference.kind == "image":
            issues.append(f"Unreviewed HTML image at {reference.path}")
        if reference.kind == "macro" and reference.name not in {
            "view-file",
            "viewpdf",
            "attachments",
        }:
            issues.append(
                f"Unresolved content macro {reference.name} at {reference.path}"
            )
    validation: Literal["present", "absent", "unreadable"] = "absent"
    for marker in soup.find_all(["p", "th", "td"]):
        if _text(marker).casefold() != "status der validierung":
            continue
        table_tag = marker.find_parent("table")
        if table_tag is not None and _is_example(table_tag):
            continue
        validation = "present" if assessments else "unreadable"
        if table_tag is None:
            issues.append(f"Validation marker outside a table at {_path(marker)}")
    context = ManualPlusContext(
        validation=validation,
        assessments=tuple(assessments),
        qualifications=tuple(qualifications),
        annotations=annotations,
        conflicts=tuple(conflicts),
        pending_references=tuple(pending),
        extraction_issues=tuple(issues),
        html_semantics_complete=not issues,
    )
    candidate = SourceEvidence.model_validate(
        {
            **source.model_dump(),
            "blocks": tuple(blocks),
            "metadata": {
                **source.metadata,
                "manual_plus": context.model_dump(mode="json"),
            },
        }
    )
    if issues:
        raise SemanticExtractionError(candidate, issues)
    return candidate


def _extract_blocks(
    soup: Tag, source_url: str, issues: list[str]
) -> tuple[list[EvidenceBlock], list[tuple[Tag, StructuredTable, str]]]:
    blocks: list[EvidenceBlock] = []
    headings: list[tuple[int, str]] = []
    tables: list[tuple[Tag, StructuredTable, str]] = []
    for item in _blocks(soup):
        if isinstance(item, NavigableString):
            parent = item.parent
            assert parent is not None
            position = next(
                i for i, child in enumerate(parent.children, 1) if child is item
            )
            path = _path(parent) + f"/text()[{position}]"
            text = " ".join(str(item).split())
            tag = parent
        else:
            tag, path, text = item, _path(item), _text(item)
        kind: Literal["heading", "paragraph", "table"] = "paragraph"
        structure = {}
        if tag.name in _HEADINGS:
            kind = "heading"
            level = int(tag.name[1])
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, text))
        elif tag.name == "table":
            kind = "table"
            try:
                table = _table(tag)
            except ValueError as error:
                issues.append(str(error))
            else:
                structure = table.model_dump(mode="json")
                tables.append((tag, table, path))
                by_path = {cell.path: cell.text for cell in table.cells}
                text = "\n".join(
                    " | ".join(by_path.get(slot or "", "") for slot in row)
                    for row in table.grid
                )
        links = [
            {
                "path": _path(a),
                "text": _text(a),
                "url": urljoin(source_url, str(a["href"])),
            }
            for a in tag.find_all("a", href=True)
        ]
        if links:
            structure["links"] = links
        if text.strip():
            blocks.append(
                EvidenceBlock(
                    extraction_version=EXTRACTION_VERSION,
                    local_key=path,
                    kind=kind,
                    content=text,
                    locator=HtmlLocator(
                        path=path, section=" > ".join(h for _, h in headings) or None
                    ),
                    structure=structure,
                )
            )
    return blocks, tables


def _qualifications(
    soup: Tag, blocks: list[EvidenceBlock], issues: list[str]
) -> list[Qualification]:
    result = []
    for macro in soup.find_all("ac:structured-macro", attrs={"ac:name": "expand"}):
        label = _macro_title(macro)
        if label not in REGISTRIES:
            continue
        path = _path(macro)
        keys = tuple(
            block.local_key
            for block in blocks
            if block.local_key.startswith(path + "/")
        )
        if not keys:
            issues.append(f"Empty registry qualification {label} at {path}")
        body = macro.find("ac:rich-text-body", recursive=False)
        text = _text(body) if body else ""
        mentioned = tuple(
            code
            for code, name in REGISTRIES.items()
            if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text)
        )
        result.append(
            Qualification(
                registry=label,
                original_label=label,
                evidence_keys=keys,
                path=path,
                mentioned_registries=mentioned,
            )
        )
    return result


def _assessments(
    tables: list[tuple[Tag, StructuredTable, str]],
    qualifications: list[Qualification],
    issues: list[str],
) -> list[RegistryAssessment]:
    result = []
    for tag, table, key in tables:
        if not any(
            cell.text.casefold() == "status der validierung" for cell in table.cells
        ) or _is_example(tag):
            continue
        tags = {_path(cell): cell for cell in tag.find_all(["td", "th"])}
        found = False
        for cell in table.cells:
            if cell.text not in REGISTRIES:
                continue
            found = True
            status_cell = tags[cell.path]
            colors = _colors(status_cell)
            if not colors:
                colors = [
                    color
                    for child in status_cell.find_all(True)
                    for color in _colors(child)
                ]
            statuses = {
                PALETTE.get(color.normalized or "", "unknown") for color in colors
            }
            status: RegistryStatus = "unknown"
            if len(statuses) == 1:
                status = statuses.pop()
            if status == "unknown":
                issues.append(
                    f"Unknown validation encoding for {cell.text} at {cell.path}"
                )
            qualification_keys = tuple(
                k
                for q in qualifications
                if q.registry == cell.text
                for k in q.evidence_keys
            )
            if status in {"qualified", "negative"} and not qualification_keys:
                issues.append(f"Missing qualification for {cell.text} at {cell.path}")
            result.append(
                RegistryAssessment(
                    registry=cell.text,
                    original_label=cell.text,
                    status=status,
                    evidence_key=key,
                    path=cell.path,
                    observed_colors=tuple(colors),
                    qualification_keys=qualification_keys,
                    struck=cell.struck,
                )
            )
        if not found:
            issues.append(f"Validation table without recognized registries at {key}")
        for cell in table.cells:
            if (
                cell.text
                and cell.text.casefold() != "status der validierung"
                and cell.text not in REGISTRIES
            ):
                issues.append(f"Unknown registry label {cell.text!r} at {cell.path}")
    return result


def _conflicts(
    assessments: list[RegistryAssessment], qualifications: list[Qualification]
) -> list[SourceConflict]:
    result = []
    for qualification in qualifications:
        other = tuple(
            code
            for code in qualification.mentioned_registries
            if code != qualification.registry
        )
        if other:
            result.append(
                SourceConflict(
                    kind="registry_label_mismatch",
                    registries=(qualification.registry, *other),
                    evidence_keys=qualification.evidence_keys,
                    description=f"Qualification labelled {qualification.original_label} names {', '.join(other)}; source attribution remains unresolved.",
                )
            )
    for registry in REGISTRIES:
        matches = [a for a in assessments if a.registry == registry]
        if len({a.status for a in matches}) > 1:
            result.append(
                SourceConflict(
                    kind="multiple_assessments",
                    registries=(registry,),
                    evidence_keys=tuple(a.evidence_key for a in matches),
                    description="Source contains different page assessments for this registry.",
                )
            )
    return result

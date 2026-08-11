"""Synchronize official oBDS schemas and the Umsetzungsleitfaden."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Final, Literal
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree

import httpx
import psycopg
from bs4 import BeautifulSoup, NavigableString, Tag
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.config import load_settings

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
DATABASE_SCHEMA_PATH: Final = PROJECT_ROOT / "db" / "init.sql"
DEFAULT_XSD_DIRECTORY: Final = Path.cwd() / "data" / "xsd"

BASISDATENSATZ_INDEX_URL: Final = "https://www.basisdatensatz.de/xml/"
CONFLUENCE_ORIGIN: Final = "https://plattform65c.atlassian.net"
CONFLUENCE_BASE_URL: Final = f"{CONFLUENCE_ORIGIN}/wiki"
CONFLUENCE_SPACE_KEY: Final = "UMK"
SOURCE_TYPE: Final = "umsetzungsleitfaden"
USER_AGENT: Final = "oBDSChat source synchronization/0.1"

_BASISDATENSATZ_HOSTS: Final = frozenset({"basisdatensatz.de", "www.basisdatensatz.de"})
_CONFLUENCE_HOSTS: Final = frozenset({"plattform65c.atlassian.net"})
_HEADING_NAMES: Final = frozenset(f"h{level}" for level in range(1, 7))
_CONTENT_NAMES: Final = frozenset({"p", "pre", "blockquote", "ac:plain-text-body"})
_IGNORED_NAMES: Final = frozenset({"script", "style", "ac:parameter"})
_SCHEMA_FILENAME_PATTERN: Final = re.compile(
    r"^oBDS_v(?P<version>3\.\d+\.\d+)\.xsd$",
    re.IGNORECASE,
)
_WHITESPACE_PATTERN: Final = re.compile(r"\s+")
_XSD_SCHEMA_TAG: Final = "{http://www.w3.org/2001/XMLSchema}schema"


class SourceSyncError(RuntimeError):
    """Raised when official source content cannot be synchronized safely."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SchemaDownload(_FrozenModel):
    """Validated metadata for one official oBDS 3.x schema."""

    version: str = Field(pattern=r"^3\.\d+\.\d+$")
    filename: str
    url: HttpUrl

    @model_validator(mode="after")
    def validate_filename(self) -> SchemaDownload:
        """Ensure the filename and parsed version describe the same schema."""
        expected_filename = f"obds_v{self.version}.xsd"
        if self.filename.casefold() != expected_filename.casefold():
            raise ValueError("Schema filename does not match its oBDS version")
        if Path(self.filename).name != self.filename:
            raise ValueError("Schema filename must not contain a directory")
        return self


class DownloadedSchema(SchemaDownload):
    """An official schema held in memory until every source has been fetched."""

    content: bytes = Field(min_length=1)


class ConfluencePage(_FrozenModel):
    """Validated source page from the Umsetzungsleitfaden space."""

    page_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: HttpUrl
    body_html: str

    @field_validator("page_id", "title", mode="after")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        """Normalize required page metadata."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Page metadata must not be empty")
        return normalized


class Document(_FrozenModel):
    """One searchable, heading-based Umsetzungsleitfaden section."""

    source_type: Literal["umsetzungsleitfaden"] = SOURCE_TYPE
    title: str = Field(min_length=1)
    section: str | None = None
    content: str = Field(min_length=1)
    url: HttpUrl
    obds_version: str | None = None

    @field_validator("title", "content", mode="after")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        """Reject empty searchable document fields."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Document text must not be empty")
        return normalized

    @field_validator("section", "obds_version", mode="after")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Normalize optional document metadata."""
        if value is None:
            return None
        return value.strip() or None


class ExtractedBlock(_FrozenModel):
    """One heading or content block extracted from Confluence storage HTML."""

    kind: Literal["heading", "content"]
    text: str = Field(min_length=1)
    heading_level: int | None = Field(default=None, ge=1, le=6)

    @model_validator(mode="after")
    def validate_heading_level(self) -> ExtractedBlock:
        """Require a level only for heading blocks."""
        if self.kind == "heading" and self.heading_level is None:
            raise ValueError("Heading blocks require a level")
        if self.kind == "content" and self.heading_level is not None:
            raise ValueError("Content blocks must not define a heading level")
        return self


class SyncOptions(_FrozenModel):
    """Validated runtime options for source synchronization."""

    database_url: str = Field(min_length=1, repr=False)
    xsd_directory: Path = DEFAULT_XSD_DIRECTORY
    timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("database_url", mode="after")
    @classmethod
    def strip_database_url(cls, value: str) -> str:
        """Normalize the database connection string."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("DATABASE_URL must not be empty")
        return normalized


class SyncResult(_FrozenModel):
    """Counts produced by a successful source synchronization."""

    schema_count: int = Field(ge=0)
    page_count: int = Field(ge=0)
    document_count: int = Field(ge=0)


class _ConfluenceLinks(_ApiModel):
    next: str | None = None
    webui: str | None = None


class _ConfluenceSpace(_ApiModel):
    id: str
    key: str


class _ConfluenceSpaceList(_ApiModel):
    results: list[_ConfluenceSpace]


class _ConfluencePageSummary(_ApiModel):
    id: str
    title: str


class _ConfluencePageList(_ApiModel):
    results: list[_ConfluencePageSummary]
    links: _ConfluenceLinks = Field(default_factory=_ConfluenceLinks, alias="_links")


class _ConfluenceStorage(_ApiModel):
    value: str


class _ConfluenceBody(_ApiModel):
    storage: _ConfluenceStorage


class _ConfluencePageDetail(_ApiModel):
    id: str
    title: str
    body: _ConfluenceBody
    links: _ConfluenceLinks = Field(alias="_links")


def create_http_client(timeout_seconds: float = 30.0) -> httpx.Client:
    """Create the HTTP client used for official public sources."""
    return httpx.Client(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers={"User-Agent": USER_AGENT},
    )


def discover_schema_downloads(client: httpx.Client) -> list[SchemaDownload]:
    """Discover every oBDS 3.x XSD linked by the official XML index."""
    response = _get(
        client,
        BASISDATENSATZ_INDEX_URL,
        allowed_hosts=_BASISDATENSATZ_HOSTS,
    )
    soup = BeautifulSoup(response.text, "html.parser")
    downloads_by_version: dict[str, SchemaDownload] = {}

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        download_url = urljoin(str(response.url), href)
        if urlsplit(download_url).hostname not in _BASISDATENSATZ_HOSTS:
            continue
        filename = Path(unquote(urlsplit(download_url).path)).name
        match = _SCHEMA_FILENAME_PATTERN.fullmatch(filename)
        if match is None:
            continue

        download = SchemaDownload(
            version=match.group("version"),
            filename=filename,
            url=download_url,
        )
        existing = downloads_by_version.get(download.version)
        if existing is not None and existing.url != download.url:
            raise SourceSyncError(
                f"Multiple schema URLs found for oBDS {download.version}"
            )
        downloads_by_version[download.version] = download

    if not downloads_by_version:
        raise SourceSyncError("No oBDS 3.x schemas found on the official XML index")

    return sorted(
        downloads_by_version.values(),
        key=lambda download: _version_key(download.version),
    )


def fetch_schemas(
    client: httpx.Client,
    downloads: Sequence[SchemaDownload],
) -> list[DownloadedSchema]:
    """Fetch and validate schemas without changing the filesystem."""
    schemas: list[DownloadedSchema] = []
    for download in downloads:
        response = _get(
            client,
            str(download.url),
            allowed_hosts=_BASISDATENSATZ_HOSTS,
        )
        content = response.content
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as error:
            raise SourceSyncError(
                f"Downloaded oBDS {download.version} schema is not valid XML"
            ) from error
        if root.tag != _XSD_SCHEMA_TAG:
            raise SourceSyncError(
                f"Downloaded oBDS {download.version} file is not an XSD schema"
            )
        schemas.append(
            DownloadedSchema(
                version=download.version,
                filename=download.filename,
                url=download.url,
                content=content,
            )
        )
    return schemas


def write_schemas(
    schemas: Sequence[DownloadedSchema],
    xsd_directory: Path,
) -> list[Path]:
    """Atomically write downloaded schemas to versioned directories."""
    written_paths: list[Path] = []
    for schema in schemas:
        destination = xsd_directory / schema.version / schema.filename
        if destination.is_file() and destination.read_bytes() == schema.content:
            written_paths.append(destination)
            continue
        _write_atomic(destination, schema.content)
        written_paths.append(destination)
    return written_paths


def fetch_confluence_pages(client: httpx.Client) -> list[ConfluencePage]:
    """Fetch every current page in the public Umsetzungsleitfaden space."""
    space_response = _get(
        client,
        f"{CONFLUENCE_BASE_URL}/api/v2/spaces",
        params={"keys": CONFLUENCE_SPACE_KEY},
        allowed_hosts=_CONFLUENCE_HOSTS,
    )
    spaces = _validated_json(
        space_response,
        _ConfluenceSpaceList,
        "Confluence space list",
    )
    matching_spaces = [
        space for space in spaces.results if space.key == CONFLUENCE_SPACE_KEY
    ]
    if len(matching_spaces) != 1:
        raise SourceSyncError(
            f"Expected exactly one Confluence space named {CONFLUENCE_SPACE_KEY}"
        )

    summaries = _fetch_page_summaries(client, matching_spaces[0].id)
    pages: list[ConfluencePage] = []
    for summary in summaries:
        response = _get(
            client,
            f"{CONFLUENCE_BASE_URL}/api/v2/pages/{summary.id}",
            params={"body-format": "storage"},
            allowed_hosts=_CONFLUENCE_HOSTS,
        )
        detail = _validated_json(
            response,
            _ConfluencePageDetail,
            f"Confluence page {summary.id}",
        )
        if detail.id != summary.id:
            raise SourceSyncError(
                f"Confluence returned the wrong page for ID {summary.id}"
            )
        if detail.links.webui is None:
            raise SourceSyncError(f"Confluence page {summary.id} has no public URL")
        pages.append(
            ConfluencePage(
                page_id=detail.id,
                title=detail.title,
                url=_confluence_url(detail.links.webui),
                body_html=detail.body.storage.value,
            )
        )

    if not pages:
        raise SourceSyncError("The Umsetzungsleitfaden space contains no pages")
    return pages


def extract_documents(pages: Sequence[ConfluencePage]) -> list[Document]:
    """Split pages into searchable sections using their heading hierarchy."""
    documents = [
        document for page in pages for document in _extract_page_documents(page)
    ]
    if not documents:
        raise SourceSyncError("No searchable Umsetzungsleitfaden content was found")
    return documents


def replace_documents(
    database_url: str,
    documents: Sequence[Document],
    *,
    schema_sql: str | None = None,
) -> None:
    """Initialize the schema and atomically replace guide documents."""
    if not documents:
        raise ValueError("documents must not be empty")
    resolved_schema_sql = _read_database_schema() if schema_sql is None else schema_sql
    rows = [
        (
            document.source_type,
            document.title,
            document.section,
            document.content,
            str(document.url),
            document.obds_version,
        )
        for document in documents
    ]

    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(resolved_schema_sql.encode(), prepare=False)
        cursor.execute(
            "DELETE FROM documents WHERE source_type = %s",
            (SOURCE_TYPE,),
        )
        cursor.executemany(
            """
            INSERT INTO documents (
                source_type,
                title,
                section,
                content,
                url,
                obds_version
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def _read_database_schema() -> str:
    """Read the database schema from the repository or installed package."""
    if DATABASE_SCHEMA_PATH.is_file():
        return DATABASE_SCHEMA_PATH.read_text(encoding="utf-8")
    packaged_schema = resources.files("scripts").joinpath("data", "init.sql")
    return packaged_schema.read_text(encoding="utf-8")


def sync_sources(
    options: SyncOptions,
    *,
    client: httpx.Client | None = None,
) -> SyncResult:
    """Fetch all sources first, then update files and PostgreSQL."""
    if client is None:
        with create_http_client(options.timeout_seconds) as owned_client:
            schemas, pages, documents = _fetch_all_sources(owned_client)
    else:
        schemas, pages, documents = _fetch_all_sources(client)

    write_schemas(schemas, options.xsd_directory)
    replace_documents(options.database_url, documents)
    return SyncResult(
        schema_count=len(schemas),
        page_count=len(pages),
        document_count=len(documents),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run source synchronization from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        help="Override the PostgreSQL URI loaded from backend settings",
    )
    parser.add_argument(
        "--xsd-directory",
        type=Path,
        default=DEFAULT_XSD_DIRECTORY,
        help="Destination root for versioned XSD files",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds",
    )
    arguments = parser.parse_args(argv)
    try:
        database_url = arguments.database_url
        if database_url is None:
            database_url = load_settings().postgres_uri
        options = SyncOptions(
            database_url=database_url,
            xsd_directory=arguments.xsd_directory,
            timeout_seconds=arguments.timeout,
        )
        result = sync_sources(options)
    except (
        OSError,
        RuntimeError,
        SourceSyncError,
        ValidationError,
        psycopg.Error,
    ) as error:
        print(f"Source synchronization failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Synchronized {result.schema_count} schemas, "
        f"{result.page_count} pages, and {result.document_count} sections."
    )
    return 0


def _fetch_all_sources(
    client: httpx.Client,
) -> tuple[list[DownloadedSchema], list[ConfluencePage], list[Document]]:
    downloads = discover_schema_downloads(client)
    schemas = fetch_schemas(client, downloads)
    pages = fetch_confluence_pages(client)
    documents = extract_documents(pages)
    return schemas, pages, documents


def _fetch_page_summaries(
    client: httpx.Client,
    space_id: str,
) -> list[_ConfluencePageSummary]:
    url = f"{CONFLUENCE_BASE_URL}/api/v2/spaces/{space_id}/pages"
    params: Mapping[str, str] | None = {"limit": "250"}
    summaries: list[_ConfluencePageSummary] = []
    seen_page_ids: set[str] = set()
    visited_urls: set[str] = set()

    while True:
        if url in visited_urls:
            raise SourceSyncError("Confluence page pagination contains a loop")
        visited_urls.add(url)
        response = _get(
            client,
            url,
            params=params,
            allowed_hosts=_CONFLUENCE_HOSTS,
        )
        page_list = _validated_json(
            response,
            _ConfluencePageList,
            "Confluence page list",
        )
        for summary in page_list.results:
            if summary.id in seen_page_ids:
                raise SourceSyncError(
                    f"Confluence returned page {summary.id} more than once"
                )
            seen_page_ids.add(summary.id)
            summaries.append(summary)

        if page_list.links.next is None:
            return summaries
        url = _confluence_url(page_list.links.next)
        params = None


def _extract_page_documents(page: ConfluencePage) -> list[Document]:
    soup = BeautifulSoup(page.body_html, "html.parser")
    heading_stack: list[tuple[int, str]] = []
    section: str | None = None
    content_blocks: list[str] = []
    documents: list[Document] = []

    def flush_section() -> None:
        if not content_blocks:
            return
        documents.append(
            Document(
                title=page.title,
                section=section,
                content="\n".join(content_blocks),
                url=page.url,
            )
        )
        content_blocks.clear()

    for block in _iter_blocks(soup):
        if block.kind == "content":
            content_blocks.append(block.text)
            continue

        flush_section()
        level = block.heading_level
        if level is None:
            raise AssertionError("Validated heading block has no level")
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, block.text))
        section = " > ".join(heading for _, heading in heading_stack)

    flush_section()
    return documents


def _iter_blocks(container: Tag) -> Iterator[ExtractedBlock]:
    for child in container.children:
        if isinstance(child, NavigableString):
            text = _normalize_text(str(child))
            if text:
                yield ExtractedBlock(kind="content", text=text)
            continue
        if not isinstance(child, Tag) or child.name is None:
            continue

        name = child.name.casefold()
        if name in _IGNORED_NAMES:
            continue
        if name in _HEADING_NAMES:
            text = _normalize_text(child.get_text(" ", strip=True))
            if text:
                yield ExtractedBlock(
                    kind="heading",
                    text=text,
                    heading_level=int(name[1]),
                )
            continue
        if name == "table":
            yield from _iter_table_rows(child)
            continue
        if name == "li":
            text = _normalize_text(child.get_text(" ", strip=True))
            if text:
                yield ExtractedBlock(kind="content", text=f"- {text}")
            continue
        if name in _CONTENT_NAMES:
            text = _normalize_text(child.get_text(" ", strip=True))
            if text:
                yield ExtractedBlock(kind="content", text=text)
            continue
        yield from _iter_blocks(child)


def _iter_table_rows(table: Tag) -> Iterator[ExtractedBlock]:
    for row in table.find_all("tr"):
        cells = [
            _normalize_text(cell.get_text(" ", strip=True))
            for cell in row.find_all(["th", "td"], recursive=False)
        ]
        text = " | ".join(cell for cell in cells if cell)
        if text:
            yield ExtractedBlock(kind="content", text=text)


def _get(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
    allowed_hosts: frozenset[str] | None = None,
) -> httpx.Response:
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise SourceSyncError(f"Failed to fetch {url}") from error
    if allowed_hosts is not None:
        _require_allowed_host(str(response.url), allowed_hosts)
    return response


def _validated_json[ModelT: BaseModel](
    response: httpx.Response,
    model: type[ModelT],
    description: str,
) -> ModelT:
    try:
        payload = response.json()
        return model.model_validate(payload)
    except (ValueError, ValidationError) as error:
        raise SourceSyncError(f"Invalid {description} response") from error


def _confluence_url(link: str) -> str:
    if link.startswith("/wiki/"):
        resolved = urljoin(CONFLUENCE_ORIGIN, link)
    else:
        resolved = urljoin(f"{CONFLUENCE_BASE_URL}/", link.lstrip("/"))
    _require_allowed_host(resolved, _CONFLUENCE_HOSTS)
    return resolved


def _require_allowed_host(url: str, allowed_hosts: frozenset[str]) -> None:
    if urlsplit(url).hostname not in allowed_hosts:
        raise SourceSyncError(f"Refusing unexpected source host in URL: {url}")


def _write_atomic(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _normalize_text(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


if __name__ == "__main__":
    raise SystemExit(main())

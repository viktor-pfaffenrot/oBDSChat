from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Self

import httpx
import pytest

from scripts import sync_sources
from scripts.sync_sources import (
    ConfluencePage,
    Document,
    SchemaDownload,
    SyncOptions,
    SyncResult,
    discover_schema_downloads,
    extract_documents,
    fetch_confluence_pages,
    fetch_schemas,
    replace_documents,
    write_schemas,
)

XSD_CONTENT = (
    b'<?xml version="1.0"?><xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" />'
)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, follow_redirects=True)


def test_discover_schema_downloads_finds_all_3_x_versions() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        html = """
        <a href="/xml/oBDS_v3.0.5.xsd">3.0.5</a>
        <a href="https://basisdatensatz.de/xml/oBDS_v3.0.0.xsd">3.0.0</a>
        <a href="/xml/ADT_GEKID_v2.2.3.xsd">2.2.3</a>
        <a href="/download/history.pdf">history</a>
        """
        return httpx.Response(200, text=html, request=request)

    with _client(httpx.MockTransport(handle_request)) as client:
        downloads = discover_schema_downloads(client)

    assert [download.version for download in downloads] == ["3.0.0", "3.0.5"]
    assert [download.filename for download in downloads] == [
        "oBDS_v3.0.0.xsd",
        "oBDS_v3.0.5.xsd",
    ]


def test_fetch_and_write_schemas_uses_version_directories(tmp_path) -> None:
    download = SchemaDownload(
        version="3.0.5",
        filename="oBDS_v3.0.5.xsd",
        url="https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd",
    )

    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=XSD_CONTENT, request=request)

    with _client(httpx.MockTransport(handle_request)) as client:
        schemas = fetch_schemas(client, [download])

    written_paths = write_schemas(schemas, tmp_path)

    expected_path = tmp_path / "3.0.5" / "oBDS_v3.0.5.xsd"
    assert written_paths == [expected_path]
    assert expected_path.read_bytes() == XSD_CONTENT


def test_fetch_confluence_pages_follows_pagination() -> None:
    def handle_request(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/wiki/api/v2/spaces":
            payload = {"results": [{"id": "42", "key": "UMK"}]}
        elif path == "/wiki/api/v2/spaces/42/pages":
            if request.url.params.get("cursor") == "next-page":
                payload = {
                    "results": [{"id": "2", "title": "Second"}],
                    "_links": {},
                }
            else:
                payload = {
                    "results": [{"id": "1", "title": "First"}],
                    "_links": {
                        "next": ("/wiki/api/v2/spaces/42/pages?cursor=next-page")
                    },
                }
        elif path == "/wiki/api/v2/pages/1":
            payload = {
                "id": "1",
                "title": "First",
                "body": {"storage": {"value": "<p>First body</p>"}},
                "_links": {"webui": "/spaces/UMK/pages/1/First"},
            }
        elif path == "/wiki/api/v2/pages/2":
            payload = {
                "id": "2",
                "title": "Second",
                "body": {"storage": {"value": "<p>Second body</p>"}},
                "_links": {"webui": "/spaces/UMK/pages/2/Second"},
            }
        else:
            raise AssertionError(f"Unexpected request: {request.url}")
        return httpx.Response(200, json=payload, request=request)

    with _client(httpx.MockTransport(handle_request)) as client:
        pages = fetch_confluence_pages(client)

    assert [page.page_id for page in pages] == ["1", "2"]
    assert str(pages[0].url) == (
        "https://plattform65c.atlassian.net/wiki/spaces/UMK/pages/1/First"
    )


def test_extract_documents_preserves_heading_hierarchy_and_tables() -> None:
    page = ConfluencePage(
        page_id="1",
        title="Diagnose",
        url="https://plattform65c.atlassian.net/wiki/spaces/UMK/pages/1/Diagnose",
        body_html="""
            <p>Einleitung</p>
            <h1>Diagnose</h1>
            <p>Allgemeiner Hinweis.</p>
            <h2>Werte</h2>
            <table>
                <tr><th>Code</th><th>Bedeutung</th></tr>
                <tr><td>A</td><td>Gesichert</td></tr>
            </table>
        """,
    )

    documents = extract_documents([page])

    assert [document.section for document in documents] == [
        None,
        "Diagnose",
        "Diagnose > Werte",
    ]
    assert documents[0].content == "Einleitung"
    assert documents[1].content == "Allgemeiner Hinweis."
    assert documents[2].content == "Code | Bedeutung\nA | Gesichert"
    assert {document.source_type for document in documents} == {"umsetzungsleitfaden"}
    assert all(document.obds_version is None for document in documents)


class _FakeCursor:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[object, object, bool | None]] = []
        self.inserted_rows: list[tuple[object, ...]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        return None

    def execute(
        self,
        query: object,
        params: object = None,
        *,
        prepare: bool | None = None,
    ) -> None:
        self.execute_calls.append((query, params, prepare))

    def executemany(
        self,
        query: str,
        params_seq: list[tuple[object, ...]],
    ) -> None:
        assert "INSERT INTO documents" in query
        self.inserted_rows.extend(params_seq)


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.exit_exception_type: type[BaseException] | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        self.exit_exception_type = exception_type

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance


def test_replace_documents_initializes_and_replaces_in_one_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(
        sync_sources.psycopg,
        "connect",
        lambda database_url: connection,
    )
    document = Document(
        title="Diagnose",
        section="Werte",
        content="Gesichert",
        url="https://example.test/diagnose",
    )

    replace_documents(
        "postgresql://test",
        [document],
        schema_sql="CREATE TABLE documents (...);",
    )

    cursor = connection.cursor_instance
    schema_query, schema_params, prepare = cursor.execute_calls[0]
    assert schema_query == b"CREATE TABLE documents (...);"
    assert schema_params is None
    assert prepare is False
    assert cursor.execute_calls[1][1] == ("umsetzungsleitfaden",)
    assert cursor.inserted_rows == [
        (
            "umsetzungsleitfaden",
            "Diagnose",
            "Werte",
            "Gesichert",
            "https://example.test/diagnose",
            None,
        )
    ]
    assert connection.exit_exception_type is None


def test_main_uses_database_uri_from_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_options: list[SyncOptions] = []
    monkeypatch.setattr(
        sync_sources,
        "load_settings",
        lambda: SimpleNamespace(postgres_uri="postgresql://configured"),
    )

    def fake_sync_sources(options: SyncOptions) -> SyncResult:
        captured_options.append(options)
        return SyncResult(schema_count=1, page_count=2, document_count=3)

    monkeypatch.setattr(sync_sources, "sync_sources", fake_sync_sources)

    exit_code = sync_sources.main(["--xsd-directory", str(tmp_path)])

    assert exit_code == 0
    assert captured_options[0].database_url == "postgresql://configured"


def test_main_database_url_flag_overrides_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_options: list[SyncOptions] = []

    def fail_if_settings_loaded() -> None:
        raise AssertionError("Settings must not load when URI is overridden")

    def fake_sync_sources(options: SyncOptions) -> SyncResult:
        captured_options.append(options)
        return SyncResult(schema_count=1, page_count=2, document_count=3)

    monkeypatch.setattr(sync_sources, "load_settings", fail_if_settings_loaded)
    monkeypatch.setattr(sync_sources, "sync_sources", fake_sync_sources)

    exit_code = sync_sources.main(
        [
            "--database-url",
            "postgresql://override",
            "--xsd-directory",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert captured_options[0].database_url == "postgresql://override"

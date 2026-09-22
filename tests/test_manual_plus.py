"""Frozen sources through public HTML import/extraction boundaries."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import pytest

from backend.evidence import (
    HtmlLocator,
    OriginalArtifact,
    SourceEvidence,
    SourceIdentity,
    get_current_source,
)
from backend.manual_plus import (
    SemanticExtractionError,
    StructuredTable,
    extract_manual_plus_page,
    manual_plus_context,
)
from scripts.sync_sources import (
    SourceSyncError,
    fetch_manual_plus_evidence,
    import_manual_plus_pages,
)

FIXTURES = Path(__file__).parent / "fixtures" / "manual_plus"


def frozen_page(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_frozen_originals_match_manifest() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    for entry in manifest:
        page = frozen_page(Path(entry["fixture"]).stem)
        assert page["id"] == entry["page_id"]
        assert str(page["version"]["number"]) == entry["source_revision"]
        assert (
            hashlib.sha256(page["body"]["storage"]["value"].encode()).hexdigest()
            == entry["storage_sha256"]
        )


def source_html(html: str) -> SourceEvidence:
    return SourceEvidence(
        identity=SourceIdentity(
            source_family="manual_plus",
            origin="https://plattform65c.atlassian.net/wiki",
            native_kind="page",
            native_id="123",
        ),
        original=OriginalArtifact(media_type="text/html", content=html.encode()),
        title="Fixture",
        url="https://plattform65c.atlassian.net/wiki/pages/123",
        source_revision="7",
        observed_at=datetime(2026, 9, 22, tzinfo=UTC),
    )


def fixture_client(*names: str) -> httpx.Client:
    pages = {p["id"]: p for p in map(frozen_page, names)}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/wiki/api/v2/spaces":
            assert request.url.params["keys"] == "Dokumentat"
            payload = {"results": [{"id": "72646923", "key": "Dokumentat"}]}
        elif request.url.path == "/wiki/api/v2/spaces/72646923/pages":
            assert request.url.params["status"] == "current"
            payload = {
                "results": [
                    {"id": p["id"], "title": p["title"], "status": "current"}
                    for p in pages.values()
                ]
            }
        else:
            assert request.url.params["body-format"] == "storage"
            payload = pages[request.url.path.rsplit("/", 1)[1]]
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_frozen_pages_keep_originals_revision_and_conflicting_qualifications() -> None:
    with fixture_client("todesmeldung", "diagnose", "overview") as client:
        batch = fetch_manual_plus_evidence(client)
    assert not batch.corpus_complete and batch.pdf_coverage == "pending"
    death, diagnosis, overview = batch.sources
    raw = frozen_page("todesmeldung")
    assert death.original.content == raw["body"]["storage"]["value"].encode()
    assert death.source_revision == str(raw["version"]["number"])
    assert death.source_modified_at == datetime.fromisoformat(
        raw["version"]["createdAt"]
    )
    context = manual_plus_context(death)
    statuses = {a.registry: a for a in context.assessments}
    assert statuses["HB"].status == statuses["SL"].status == "negative"
    assert statuses["SL"].struck and statuses["SH"].status == "positive"
    (conflict,) = context.conflicts
    assert conflict.registries == ("SL", "SH")
    by_key = {b.local_key: b for b in death.blocks}
    assert "Schleswig-Holstein" in " ".join(
        by_key[k].content for k in statuses["SL"].qualification_keys
    )
    assert all(k in by_key for k in conflict.evidence_keys)
    assert any(
        a.meaning == "change" and a.text == "müssen" for a in context.annotations
    )
    assert any(
        a.normalized == "#d3f1a7" and a.meaning == "uninterpreted"
        for a in context.annotations
    )
    assert death.applicability_date is None and death.publication_edition is None
    assert len([b for b in death.blocks if "Hinweis: Änderungen" in b.content]) == 1
    assert {a.registry: a.status for a in manual_plus_context(diagnosis).assessments}[
        "HE"
    ] == "unreviewed"
    assert manual_plus_context(overview).assessments == ()
    assert manual_plus_context(overview).validation == "absent"
    assert any("Status der Validierung" in b.content for b in overview.blocks)


def validation_html(color: str) -> str:
    return f"""<table><tr><th colspan="4">Status der Validierung</th></tr><tr>
    <td {color}>BW</td><td bgcolor="#FFF0B3">BY</td>
    <td style="background-color: rgb(255, 189, 173)"><del>HB</del></td>
    <td data-highlight-colour="#f4f5f7">HE</td></tr></table>
    <h1>Guidance</h1><p>Keep this rule.</p>
    <h1>Landesspezifische Anmerkungen bei gelber und roter Validierung</h1>
    <ac:structured-macro ac:name="expand"><ac:parameter ac:name="title">BY</ac:parameter>
    <ac:rich-text-body><p>Qualification for Bayern. <a href="#rule">Rule</a></p></ac:rich-text-body></ac:structured-macro>
    <ac:structured-macro ac:name="expand"><ac:parameter ac:name="title">HB</ac:parameter>
    <ac:rich-text-body><p>Rejection for Bremen.</p></ac:rich-text-body></ac:structured-macro>"""


@pytest.mark.parametrize(
    "color",
    [
        'data-highlight-colour="#abf5d1"',
        'bgcolor="#ABF5D1"',
        'style="background: rgb(171,245,209)"',
        'style="background-color: rgba(171, 245, 209, 1) !important"',
    ],
)
def test_equivalent_encodings_and_required_qualification_links(color: str) -> None:
    source = extract_manual_plus_page(source_html(validation_html(color)))
    context = manual_plus_context(source)
    assert [a.status for a in context.assessments] == [
        "positive",
        "qualified",
        "negative",
        "unreviewed",
    ]
    qualified = context.assessments[1]
    assert qualified.qualification_keys
    block = next(
        b for b in source.blocks if b.local_key == qualified.qualification_keys[0]
    )
    assert "Qualification for Bayern" in block.content
    assert block.structure["links"] == [
        {
            "path": qualified.qualification_keys[0] + "/a[1]",
            "text": "Rule",
            "url": "https://plattform65c.atlassian.net/wiki/pages/123#rule",
        }
    ]
    assert not context.conflicts


@pytest.mark.parametrize(
    "encoding",
    [
        'style="background: rebeccapurple"',
        "",
        'data-highlight-colour="#abf5d1" style="background:#ffbdad"',
        'style="background-color: rgba(171,245,209,0.5)"',
    ],
)
def test_unknown_or_missing_validation_fails_without_becoming_gray(
    encoding: str,
) -> None:
    original = source_html(validation_html(encoding))
    with pytest.raises(SemanticExtractionError, match="Unknown validation") as caught:
        extract_manual_plus_page(original)
    candidate = caught.value.candidate
    assert candidate.original == original.original
    context = manual_plus_context(candidate)
    assert context.assessments[0].status == "unknown"
    assert not context.html_semantics_complete and not context.corpus_complete


def test_missing_qualification_rejects_candidate() -> None:
    html = validation_html('bgcolor="#abf5d1"').split("<h1>Landesspezifische")[0]
    with pytest.raises(SemanticExtractionError, match="Missing qualification for BY"):
        extract_manual_plus_page(source_html(html))


def test_frozen_yellow_and_source_missing_qualification() -> None:
    with fixture_client("systemische-therapie") as client:
        (source,) = fetch_manual_plus_evidence(client).sources
    context = manual_plus_context(source)
    qualified = [a for a in context.assessments if a.status == "qualified"]
    assert {a.registry for a in qualified} == {"SL", "ST"}
    assert all(a.qualification_keys for a in qualified)
    assert all(
        k in {b.local_key for b in source.blocks}
        for a in qualified
        for k in a.qualification_keys
    )
    with (
        fixture_client("tumorzuordnung") as client,
        pytest.raises(SourceSyncError, match="Missing qualification for HH"),
    ):
        fetch_manual_plus_evidence(client)


def test_empty_landing_page_and_unreviewed_image() -> None:
    source = extract_manual_plus_page(source_html("<p />"))
    assert source.blocks == ()
    assert manual_plus_context(source).validation == "absent"
    with pytest.raises(SemanticExtractionError, match="Unreviewed HTML image"):
        extract_manual_plus_page(
            source_html(
                '<p>Diagram follows.</p><ac:image><ri:attachment ri:filename="rule.png" /></ac:image>'
            )
        )


@pytest.mark.db_smoke
def test_public_import_roundtrip_replacement_and_failed_candidate(
    database_url: str,
) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            (Path(__file__).parents[1] / "db" / "init.sql").read_bytes(), prepare=False
        )
        with fixture_client("todesmeldung", "systemische-therapie") as client:
            ids = import_manual_plus_pages(connection, client=client)
        initial = [get_current_source(connection, source_id) for source_id in ids]
        assert all(source is not None for source in initial)
        for source in initial:
            assert source is not None
            context = manual_plus_context(source.observation)
            assert not context.corpus_complete
            assert all(
                k in {b.local_key for b in source.evidence}
                for a in context.assessments
                for k in a.qualification_keys
            )
        death = initial[0]
        assert death is not None
        assert (
            death.observation.original.content
            == frozen_page("todesmeldung")["body"]["storage"]["value"].encode()
        )
        assert manual_plus_context(death.observation).conflicts[0].registries == (
            "SL",
            "SH",
        )
        with fixture_client("todesmeldung", "systemische-therapie") as client:
            assert import_manual_plus_pages(connection, client=client) == ids
        current = get_current_source(connection, ids[0])
        assert current is not None
        assert current.evidence[0].id != death.evidence[0].id
        assert current.observation.original == death.observation.original
        with (
            fixture_client("overview", "tumorzuordnung") as client,
            pytest.raises(SourceSyncError, match="Missing qualification for HH"),
        ):
            import_manual_plus_pages(connection, client=client)
        assert get_current_source(connection, ids[0]) == current
        assert connection.execute("SELECT count(*) FROM sources").fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM documents WHERE source_type = 'manual_plus'"
        ).fetchone() == (0,)


def test_tables_preserve_headers_empty_and_spanning_cells() -> None:
    html = """<h1>Table</h1><table>
    <tr><th rowspan="2">Situation</th><th colspan="2">Values</th></tr>
    <tr><th>A</th><th>B</th></tr>
    <tr><th>First</th><td></td><td bgcolor="#abf5d1">BW</td></tr>
    <tr><th>Second</th><td colspan="2">Shared</td></tr></table>"""
    source = extract_manual_plus_page(source_html(html))
    block = source.blocks[1]
    table = StructuredTable.model_validate(block.structure)
    by_path = {c.path: c for c in table.cells}
    assert table.grid[0][0] == table.grid[1][0]
    assert table.grid[0][1] == table.grid[0][2]
    assert table.grid[3][1] == table.grid[3][2]
    empty_path, bw_path = table.grid[2][1:3]
    assert empty_path is not None and bw_path is not None
    assert by_path[empty_path].text == ""
    bw = by_path[bw_path]
    assert {by_path[p].text for p in bw.headers} == {"Values", "B", "First"}
    assert isinstance(block.locator, HtmlLocator) and block.locator.section == "Table"
    assert "First |  | BW" in block.content
    assert manual_plus_context(source).assessments == ()


def test_absent_status_decorative_colors_and_pending_pdf_are_distinct() -> None:
    source = extract_manual_plus_page(
        source_html("""
    <p style="background:purple">Decorative.</p>
    <p><span style="background:#c6edfb">Changed.</span></p>
    <ac:structured-macro ac:name="view-file"><ac:parameter ac:name="name">
    <ri:attachment ri:filename="Decision.pdf" /></ac:parameter></ac:structured-macro>""")
    )
    context = manual_plus_context(source)
    assert context.validation == "absent" and context.html_semantics_complete
    assert context.pdf_coverage == "pending" and not context.corpus_complete
    assert context.annotations[0].normalized is None
    assert context.annotations[0].meaning == "uninterpreted"
    assert context.annotations[1].meaning == "change"
    assert any(
        r.kind == "attachment" and r.name == "Decision.pdf"
        for r in context.pending_references
    )


def test_unresolved_content_macro_and_broken_table_fail_clearly() -> None:
    for html in [
        '<ac:structured-macro ac:name="include" />',
        '<table><tr><td rowspan="5">Lost relationship</td></tr></table>',
    ]:
        with pytest.raises(SemanticExtractionError):
            extract_manual_plus_page(source_html(html))


def test_unreadable_validation_is_not_absent() -> None:
    with pytest.raises(SemanticExtractionError) as caught:
        extract_manual_plus_page(source_html("<p>Status der Validierung</p><p>BW</p>"))
    assert manual_plus_context(caught.value.candidate).validation == "unreadable"


def test_nested_table_relationships_and_hidden_markup() -> None:
    source = extract_manual_plus_page(
        source_html("""<!-- hidden -->
    <table><tr><th>Outer</th><td><table><tr><th>Inner</th><th>Value</th></tr>
    <tr><td>Code</td><td>5<!-- hidden --></td></tr></table>
    <ac:structured-macro ac:name="info"><ac:parameter ac:name="icon">false</ac:parameter>
    <ac:rich-text-body><p>Visible note</p></ac:rich-text-body></ac:structured-macro>
    </td></tr></table>""")
    )
    assert len(source.blocks) == 2
    inner = StructuredTable.model_validate(source.blocks[1].structure)
    assert [cell.text for cell in inner.cells] == ["Inner", "Value", "Code", "5"]
    assert all(
        "hidden" not in block.content and "false" not in block.content
        for block in source.blocks
    )


def test_conflicting_assessments_are_preserved() -> None:
    html = validation_html('bgcolor="#abf5d1"')
    html += '<table><tr><th>Status der Validierung</th></tr><tr><td bgcolor="#f4f5f7">BW</td></tr></table>'
    context = manual_plus_context(extract_manual_plus_page(source_html(html)))
    assert context.conflicts[0].kind == "multiple_assessments"
    assert context.html_semantics_complete


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "status",
        "revision",
        "identity",
        "space",
        "listing",
        "duplicate",
        "loop",
        "external",
        "redirect",
        "http",
    ],
)
def test_current_discovery_pagination_archives_and_incomplete_inputs(
    failure: str | None,
) -> None:
    page = frozen_page("overview")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "plattform65c.atlassian.net"
        requested.append(request.url.path)
        if request.url.path == "/wiki/api/v2/spaces":
            payload = {"results": [{"id": "72646923", "key": "Dokumentat"}]}
        elif request.url.path.endswith("/pages"):
            if request.url.params.get("cursor"):
                if failure == "http":
                    return httpx.Response(503)
                payload = {
                    "results": [{"id": "99", "title": "Old", "status": "archived"}]
                }
                if failure == "duplicate":
                    payload["results"] = [
                        {"id": page["id"], "title": page["title"], "status": "current"}
                    ]
                if failure == "loop":
                    payload["_links"] = {
                        "next": "/wiki/api/v2/spaces/72646923/pages?cursor=next"
                    }
            else:
                payload = {
                    "results": [
                        {"id": page["id"], "title": page["title"], "status": "current"}
                    ],
                    "_links": {
                        "next": "/wiki/api/v2/spaces/72646923/pages?cursor=next"
                    },
                }
                if failure == "listing":
                    payload["results"][0].pop("status")
                if failure == "external":
                    payload["_links"]["next"] = "https://evil.test/pages"
        else:
            payload = page.copy()
            if failure == "redirect":
                return httpx.Response(
                    302, headers={"Location": "https://evil.test/page"}
                )
            if failure == "status":
                payload["status"] = "archived"
            elif failure == "revision":
                payload.pop("version")
            elif failure == "identity":
                payload["id"] = "other"
            elif failure == "space":
                payload["spaceId"] = "123"
        return httpx.Response(200, json=payload)

    with httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        if failure:
            with pytest.raises(SourceSyncError):
                fetch_manual_plus_evidence(client)
        else:
            batch = fetch_manual_plus_evidence(client)
            assert len(batch.sources) == 1
            assert batch.sources[0].identity.native_id == page["id"]
    assert not any(path.endswith("/99") for path in requested)

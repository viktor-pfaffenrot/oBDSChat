"""Persistence contracts against a disposable PostgreSQL schema."""

from datetime import UTC, datetime

import psycopg
import pytest
from pydantic import ValidationError

from backend import search
from backend.evidence import (
    EvidenceBlock,
    HtmlLocator,
    OriginalArtifact,
    PdfLocator,
    SourceEvidence,
    SourceIdentity,
    get_current_source,
    replace_source,
)
from scripts.sync_sources import Document, replace_documents


def _guide_document(**updates: object) -> Document:
    values = {
        "title": "Meldungen",
        "section": "Diagnose",
        "content": "Hinweise zur Meldung einer Diagnose.",
        "url": "https://example.test/guide",
        **updates,
    }
    return Document.model_validate(values)


def _observation(**updates: object) -> SourceEvidence:
    values = {
        "identity": SourceIdentity(
            source_family="manual_plus",
            origin="https://example.test/wiki/",
            native_kind="page",
            native_id="123",
        ),
        "original": OriginalArtifact(
            media_type="text/html", content=b"<p>Original evidence</p>"
        ),
        "source_revision": "7",
        "title": "Documentation guidance",
        "url": "https://example.test/wiki/pages/123/Guidance",
        "observed_at": datetime(2026, 9, 20, tzinfo=UTC),
        "blocks": (
            EvidenceBlock(
                extraction_version="fixture-v1",
                local_key="paragraph-1",
                kind="paragraph",
                content="Original evidence",
                locator=HtmlLocator(path="/p[1]"),
            ),
        ),
        **updates,
    }
    return SourceEvidence.model_validate(values)


def test_source_rejects_wrong_locator_and_duplicate_evidence() -> None:
    block = _observation().blocks[0]
    with pytest.raises(ValidationError, match="locator"):
        _observation(
            blocks=(block.model_copy(update={"locator": PdfLocator(physical_page=1)}),)
        )
    with pytest.raises(ValidationError, match="Duplicate evidence"):
        _observation(blocks=(block, block))
    with pytest.raises(ValidationError):
        PdfLocator(physical_page=0)
    with pytest.raises(ValidationError):
        _observation(observed_at="2026-09-20T00:00:00")


@pytest.mark.db_smoke
def test_fresh_and_repeated_sync_preserve_public_guide_queries(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [
        _guide_document(),
        _guide_document(title="Aktuelle Meldung", obds_version="3.0.5"),
        _guide_document(title="Alte Meldung", obds_version="3.0.4"),
    ]
    monkeypatch.setattr(
        search, "connect_database", lambda: psycopg.connect(database_url)
    )
    for _ in range(2):
        replace_documents(database_url, documents)
        results = search.search_umsetzungsleitfaden("Meldung", version="3.0.5")
        assert {result.title for result in results} == {"Meldungen", "Aktuelle Meldung"}
        for result in results:
            excerpt = search.get_source_excerpt(result.source_id)
            assert excerpt is not None
            assert excerpt.content == documents[0].content
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM sources").fetchone() == (0,)


@pytest.mark.db_smoke
def test_replacement_keeps_identity_without_history(database_url: str) -> None:
    replace_documents(database_url, [_guide_document()])
    observation = _observation()
    with psycopg.connect(database_url) as connection:
        source_id = replace_source(connection, observation)
        first = get_current_source(connection, source_id)
        assert first is not None
        assert first.observation == observation
        updated = _observation(
            title="Renamed",
            url="https://example.test/new",
            observed_at=datetime(2026, 9, 21, tzinfo=UTC),
            source_revision="8",
            original=OriginalArtifact(media_type="text/html", content=b"<p>New</p>"),
            blocks=(observation.blocks[0].model_copy(update={"content": "New"}),),
        )
        for _ in range(2):
            assert replace_source(connection, updated) == source_id
            current = get_current_source(connection, source_id)
            assert current is not None
            assert current.observation == updated
            assert current.evidence[0].id != first.evidence[0].id
            assert connection.execute("SELECT count(*) FROM sources").fetchone() == (1,)
            assert connection.execute(
                "SELECT count(*) FROM evidence_blocks"
            ).fetchone() == (1,)
            assert (
                connection.execute(
                    "SELECT id FROM evidence_blocks WHERE id = %s",
                    (first.evidence[0].id,),
                ).fetchone()
                is None
            )
            assert connection.execute(
                "SELECT content_sha256 FROM sources"
            ).fetchone() == (updated.original.sha256,)
    replace_documents(database_url, [_guide_document(content="New guide text")])
    with psycopg.connect(database_url) as connection:
        assert get_current_source(connection, source_id) == current
        replace_source(connection, _observation(blocks=()))
        empty = get_current_source(connection, source_id)
        assert empty is not None and empty.evidence == ()
        connection.execute("DELETE FROM sources WHERE id = %s", (source_id,))
        assert get_current_source(connection, source_id) is None
        assert connection.execute(
            "SELECT count(*) FROM evidence_blocks"
        ).fetchone() == (0,)


@pytest.mark.db_smoke
def test_pdf_bytes_and_native_namespaces(database_url: str) -> None:
    replace_documents(database_url, [_guide_document()])
    original_bytes = b"%PDF-1.7\n\x00\xff binary fixture"
    observation = _observation(
        original=OriginalArtifact(media_type="application/pdf", content=original_bytes),
        blocks=(
            EvidenceBlock(
                extraction_version="reviewed-v1",
                local_key="diagram",
                kind="diagram",
                content="Ambiguous connector preserved.",
                locator=PdfLocator(physical_page=5),
                structure={"uncertain": True},
            ),
        ),
    )
    identities = [
        observation.identity,
        observation.identity.model_copy(update={"native_kind": "attachment"}),
        observation.identity.model_copy(update={"native_id": "456"}),
        observation.identity.model_copy(
            update={"source_family": "umsetzungsleitfaden"}
        ),
        SourceIdentity(
            source_family="manual_plus",
            origin="https://other.test/",
            native_kind="page",
            native_id="123",
        ),
    ]
    with psycopg.connect(database_url) as connection:
        ids = set()
        for identity in identities:
            item = observation.model_copy(update={"identity": identity})
            source_id = replace_source(connection, item)
            ids.add(source_id)
            current = get_current_source(connection, source_id)
            assert current is not None
            assert current.observation == item
            assert current.observation.original.content == original_bytes
            assert current.evidence[0].locator == PdfLocator(physical_page=5)
        assert len(ids) == len(identities)


@pytest.mark.db_smoke
def test_failed_replacement_and_outer_rollback_keep_current(database_url: str) -> None:
    replace_documents(database_url, [_guide_document()])
    with psycopg.connect(database_url) as connection:
        source_id = replace_source(connection, _observation())
        first = get_current_source(connection, source_id)
        # Force a database failure after updating the source and deleting old blocks.
        connection.execute(
            "ALTER TABLE evidence_blocks ADD CONSTRAINT reject_bad CHECK (content <> 'bad')"
        )
        bad = _observation().blocks[0].model_copy(update={"content": "bad"})
        with pytest.raises(psycopg.IntegrityError):
            replace_source(connection, _observation(title="Changed", blocks=(bad,)))
        assert get_current_source(connection, source_id) == first
        with pytest.raises(RuntimeError), connection.transaction():
            replace_source(connection, _observation(title="Rolled back"))
            raise RuntimeError("Candidate failed")
        assert get_current_source(connection, source_id) == first
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute("UPDATE sources SET content_sha256 = %s", ("0" * 64,))
        assert get_current_source(connection, source_id) == first

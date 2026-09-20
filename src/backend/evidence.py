"""Validated current originals and evidence, separate from searchable documents."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    model_validator,
)

SourceFamily = Literal["umsetzungsleitfaden", "manual_plus"]
RequiredText = Annotated[str, Field(min_length=1, pattern=r"\S")]


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceIdentity(_EvidenceModel):
    """A native page or attachment key within a source namespace."""

    source_family: SourceFamily
    origin: HttpUrl
    native_kind: Literal["page", "attachment"]
    native_id: RequiredText


class OriginalArtifact(_EvidenceModel):
    """Original storage HTML encoded as UTF-8, or unchanged PDF bytes."""

    media_type: Literal["text/html", "application/pdf"]
    content: bytes = Field(repr=False)

    @property
    def sha256(self) -> str:
        """Identify original bytes without treating a hash as source identity."""
        return hashlib.sha256(self.content).hexdigest()


class HtmlLocator(_EvidenceModel):
    """A structural location within the retained storage HTML."""

    format: Literal["html"] = "html"
    path: RequiredText
    section: str | None = None


class PdfLocator(_EvidenceModel):
    """A one-based physical PDF page, independent of printed numbering."""

    format: Literal["pdf"] = "pdf"
    physical_page: int = Field(ge=1)
    label: str | None = None


class EvidenceBlock(_EvidenceModel):
    """A current extraction result with an exact original-artifact locator."""

    extraction_version: RequiredText
    local_key: RequiredText
    kind: Literal["heading", "paragraph", "table", "diagram"]
    content: RequiredText
    locator: Annotated[HtmlLocator | PdfLocator, Field(discriminator="format")]
    structure: dict[str, JsonValue] = Field(default_factory=dict)


class SourceEvidence(_EvidenceModel):
    """Observed original and metadata; no inferred revision or applicability dates."""

    identity: SourceIdentity
    original: OriginalArtifact
    source_revision: RequiredText | None = None
    title: RequiredText
    url: HttpUrl
    observed_at: AwareDatetime
    source_modified_at: AwareDatetime | None = None
    publication_edition: RequiredText | None = None
    decision_date: date | None = None
    applicability_date: date | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    blocks: tuple[EvidenceBlock, ...] = ()

    @model_validator(mode="after")
    def validate_blocks(self) -> SourceEvidence:
        """Require unique extraction keys and locators matching the artifact type."""
        keys = [block.local_key for block in self.blocks]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate evidence key within a source")
        for block in self.blocks:
            is_pdf = isinstance(block.locator, PdfLocator)
            if is_pdf != (self.original.media_type == "application/pdf"):
                raise ValueError("Evidence locator does not match original media type")
        return self


class RetainedEvidence(EvidenceBlock):
    id: UUID


class CurrentSource(_EvidenceModel):
    """Stable source identity with only the current original and evidence."""

    id: UUID
    observation: SourceEvidence
    evidence: tuple[RetainedEvidence, ...]


def replace_source(
    connection: psycopg.Connection[Any], observation: SourceEvidence
) -> UUID:
    """Replace one current source and all its evidence atomically.

    The caller owns the outer transaction and can group replacements/deletions.
    Candidate validation, family publication, and readiness belong to refresh.
    Every replacement issues fresh evidence IDs, even for unchanged text.
    """
    identity = observation.identity
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            INSERT INTO sources (
                id, source_family, origin, native_kind, native_id,
                original_content, content_sha256, media_type, source_revision,
                title, url, observed_at, source_modified_at, publication_edition,
                decision_date, applicability_date, metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (source_family, origin, native_kind, native_id)
            DO UPDATE SET
                original_content = EXCLUDED.original_content,
                content_sha256 = EXCLUDED.content_sha256,
                media_type = EXCLUDED.media_type,
                source_revision = EXCLUDED.source_revision,
                title = EXCLUDED.title,
                url = EXCLUDED.url,
                observed_at = EXCLUDED.observed_at,
                source_modified_at = EXCLUDED.source_modified_at,
                publication_edition = EXCLUDED.publication_edition,
                decision_date = EXCLUDED.decision_date,
                applicability_date = EXCLUDED.applicability_date,
                metadata = EXCLUDED.metadata
            RETURNING id
            """,
            (
                uuid4(),
                identity.source_family,
                str(identity.origin),
                identity.native_kind,
                identity.native_id,
                observation.original.content,
                observation.original.sha256,
                observation.original.media_type,
                observation.source_revision,
                observation.title,
                str(observation.url),
                observation.observed_at,
                observation.source_modified_at,
                observation.publication_edition,
                observation.decision_date,
                observation.applicability_date,
                Jsonb(observation.metadata),
            ),
        )
        row = cursor.fetchone()
        assert row is not None
        source_id = row["id"]
        cursor.execute("DELETE FROM evidence_blocks WHERE source_id = %s", (source_id,))
        cursor.executemany(
            """
            INSERT INTO evidence_blocks (
                id, source_id, extraction_version, local_key, kind, content,
                locator, structure
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    uuid4(),
                    source_id,
                    block.extraction_version,
                    block.local_key,
                    block.kind,
                    block.content,
                    Jsonb(block.locator.model_dump(mode="json")),
                    Jsonb(block.structure),
                )
                for block in observation.blocks
            ],
        )
    return source_id


def get_current_source(
    connection: psycopg.Connection[Any], source_id: UUID
) -> CurrentSource | None:
    """Read original and evidence in one statement for a consistent current view."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT s.*, COALESCE(
                (SELECT jsonb_agg(to_jsonb(b) - 'source_id' ORDER BY b.local_key)
                 FROM evidence_blocks b WHERE b.source_id = s.id), '[]'::jsonb
            ) AS evidence
            FROM sources s WHERE s.id = %s
            """,
            (source_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    evidence = tuple(
        RetainedEvidence.model_validate(block) for block in row["evidence"]
    )
    observation = SourceEvidence(
        identity=SourceIdentity(
            source_family=row["source_family"],
            origin=row["origin"],
            native_kind=row["native_kind"],
            native_id=row["native_id"],
        ),
        original=OriginalArtifact(
            media_type=row["media_type"], content=bytes(row["original_content"])
        ),
        source_revision=row["source_revision"],
        title=row["title"],
        url=row["url"],
        observed_at=row["observed_at"],
        source_modified_at=row["source_modified_at"],
        publication_edition=row["publication_edition"],
        decision_date=row["decision_date"],
        applicability_date=row["applicability_date"],
        metadata=row["metadata"],
        blocks=tuple(
            EvidenceBlock.model_validate(block.model_dump(exclude={"id"}))
            for block in evidence
        ),
    )
    return CurrentSource(id=row["id"], observation=observation, evidence=evidence)

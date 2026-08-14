"""BM25 search over prose sources stored in PostgreSQL."""

from typing import Final

from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from backend.db import connect_database

SOURCE_TYPE: Final = "umsetzungsleitfaden"
EXCERPT_LENGTH: Final = 600

_SEARCH_QUERY: Final = f"""
    SELECT
        id AS source_id,
        source_type,
        title,
        section,
        LEFT(content, {EXCERPT_LENGTH}) AS excerpt,
        url,
        obds_version,
        pdb.score(id)::double precision AS score
    FROM documents
    WHERE source_type = %s
      AND (
          title ||| (%s::text)::pdb.boost(3)
          OR section ||| (%s::text)::pdb.boost(2)
          OR content ||| %s
      )
      AND (
          %s::text IS NULL
          OR obds_version IS NULL
          OR obds_version = %s
      )
    ORDER BY score DESC, id
    LIMIT %s
"""

_SOURCE_QUERY: Final = """
    SELECT
        id AS source_id,
        source_type,
        title,
        section,
        content,
        url,
        obds_version
    FROM documents
    WHERE id = %s
"""


class SearchResult(BaseModel):
    """One ranked Umsetzungsleitfaden search result."""

    model_config = ConfigDict(frozen=True)

    source_id: int = Field(gt=0)
    source_type: str
    title: str
    section: str | None = None
    excerpt: str
    url: HttpUrl
    obds_version: str | None = None
    score: float


class SourceExcerpt(BaseModel):
    """Complete stored content and citation metadata for one source."""

    model_config = ConfigDict(frozen=True)

    source_id: int = Field(gt=0)
    source_type: str
    title: str
    section: str | None = None
    content: str
    url: HttpUrl
    obds_version: str | None = None


def search_umsetzungsleitfaden(
    query: str,
    version: str | None = None,
    limit: int = 5,
) -> list[SearchResult]:
    """Return BM25-ranked guide sections matching a German text query."""
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Search query must not be empty")
    if limit < 1:
        raise ValueError("Search limit must be at least 1")

    normalized_version = _normalize_version(version)
    parameters = (
        SOURCE_TYPE,
        normalized_query,
        normalized_query,
        normalized_query,
        normalized_version,
        normalized_version,
        limit,
    )
    with (
        connect_database() as connection,
        connection.cursor(row_factory=dict_row) as cursor,
    ):
        cursor.execute(_SEARCH_QUERY, parameters)
        return [SearchResult.model_validate(row) for row in cursor.fetchall()]


def get_source_excerpt(source_id: int) -> SourceExcerpt | None:
    """Return complete content for a stored source, or ``None`` when absent."""
    if source_id < 1:
        raise ValueError("source_id must be at least 1")

    with (
        connect_database() as connection,
        connection.cursor(row_factory=dict_row) as cursor,
    ):
        cursor.execute(_SOURCE_QUERY, (source_id,))
        row = cursor.fetchone()
    if row is None:
        return None
    return SourceExcerpt.model_validate(row)


def _normalize_version(version: str | None) -> str | None:
    if version is None:
        return None
    normalized_version = version.strip()
    if not normalized_version:
        raise ValueError("Search version must not be empty")
    return normalized_version

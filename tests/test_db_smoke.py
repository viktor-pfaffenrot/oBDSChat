"""Optional PostgreSQL smoke test for the document schema."""

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

DATABASE_SCHEMA_PATH = Path(__file__).parents[1] / "db" / "init.sql"


@pytest.mark.db_smoke
def test_document_schema_supports_german_full_text_search() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is not configured")

    schema_name = f"obdschat_smoke_{uuid.uuid4().hex}"
    schema_sql = DATABASE_SCHEMA_PATH.read_text(encoding="utf-8")
    connection = psycopg.connect(database_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name))
            )
            cursor.execute(
                sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(
                    sql.Identifier(schema_name)
                )
            )
            cursor.execute(schema_sql.encode(), prepare=False)
            cursor.execute(
                """
                INSERT INTO documents (
                    source_type,
                    title,
                    section,
                    content,
                    url
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    "umsetzungsleitfaden",
                    "Diagnosesicherung",
                    "Zulässige Werte",
                    "Die Diagnose ist histologisch gesichert.",
                    "https://example.test/diagnose",
                ),
            )
            cursor.execute(
                """
                SELECT (
                    setweight(to_tsvector('german', title), 'A')
                    || setweight(
                        to_tsvector('german', COALESCE(section, '')),
                        'B'
                    )
                    || setweight(to_tsvector('german', content), 'C')
                ) @@ websearch_to_tsquery('german', %s)
                FROM documents
                """,
                ("Diagnosesicherung",),
            )
            search_result = cursor.fetchone()
            assert search_result is not None
            assert search_result[0] is True

            cursor.execute(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = %s
                  AND indexname = 'documents_full_text_idx'
                """,
                (schema_name,),
            )
            index_result = cursor.fetchone()
            assert index_result is not None
            assert "using gin" in index_result[0].casefold()
    finally:
        connection.rollback()
        connection.close()

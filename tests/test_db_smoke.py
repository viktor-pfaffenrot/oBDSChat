"""Optional PostgreSQL smoke test for BM25 document search."""

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

DATABASE_SCHEMA_PATH = Path(__file__).parents[1] / "db" / "init.sql"


@pytest.mark.db_smoke
def test_document_schema_supports_german_bm25_search() -> None:
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
            cursor.execute(
                """
                CREATE TABLE documents (
                    id BIGSERIAL PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    section TEXT,
                    content TEXT NOT NULL,
                    url TEXT NOT NULL,
                    obds_version TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX documents_full_text_idx
                ON documents
                USING GIN (to_tsvector('german', content))
                """
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
                VALUES
                    (%s, %s, %s, %s, %s),
                    (%s, %s, %s, %s, %s),
                    (%s, %s, %s, %s, %s),
                    (%s, %s, %s, %s, %s)
                RETURNING id, obds_version
                """,
                (
                    "umsetzungsleitfaden",
                    "Meldungen",
                    "Allgemein",
                    "Versionsübergreifende Hinweise.",
                    "https://example.test/generic",
                    "umsetzungsleitfaden",
                    "Versionshinweis",
                    "Details",
                    "Diese Meldung gilt für Version 3.0.5.",
                    "https://example.test/current",
                    "umsetzungsleitfaden",
                    "Alter Versionshinweis",
                    "Details",
                    "Diese Meldung gilt für Version 3.0.4.",
                    "https://example.test/old",
                    "umsetzungsleitfaden",
                    "Anderes Thema",
                    "Details",
                    "Kein passender Begriff.",
                    "https://example.test/unrelated",
                ),
            )
            inserted_rows = cursor.fetchall()
            generic_id = inserted_rows[0][0]
            current_version_id = inserted_rows[1][0]
            cursor.execute(
                "UPDATE documents SET obds_version = %s WHERE id = %s",
                ("3.0.5", current_version_id),
            )
            cursor.execute(
                "UPDATE documents SET obds_version = %s WHERE id = %s",
                ("3.0.4", inserted_rows[2][0]),
            )

            cursor.execute(
                """
                SELECT id, pdb.score(id)::double precision AS score
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
                """,
                (
                    "umsetzungsleitfaden",
                    "Meldung",
                    "Meldung",
                    "Meldung",
                    "3.0.5",
                    "3.0.5",
                    5,
                ),
            )
            search_results = cursor.fetchall()
            assert [result[0] for result in search_results] == [
                generic_id,
                current_version_id,
            ]

            cursor.execute(
                "SELECT content FROM documents WHERE id = %s",
                (current_version_id,),
            )
            excerpt_result = cursor.fetchone()
            assert excerpt_result == ("Diese Meldung gilt für Version 3.0.5.",)

            cursor.execute(
                """
                SELECT access_method.amname, pg_get_indexdef(index_relation.oid)
                FROM pg_class AS index_relation
                JOIN pg_namespace AS namespace
                    ON namespace.oid = index_relation.relnamespace
                JOIN pg_am AS access_method
                    ON access_method.oid = index_relation.relam
                WHERE namespace.nspname = %s
                  AND index_relation.relname = 'documents_full_text_idx'
                """,
                (schema_name,),
            )
            index_result = cursor.fetchone()
            assert index_result is not None
            assert index_result[0] in {"bm25", "paradedb"}
            assert "stemmer=german" in index_result[1].casefold()
    finally:
        connection.rollback()
        connection.close()

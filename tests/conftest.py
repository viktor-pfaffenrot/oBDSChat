"""Shared disposable PostgreSQL schema for persistence integration tests."""

import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo


@pytest.fixture
def database_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = f"evidence_test_{uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS pg_search CASCADE")
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield make_conninfo(url, options=f"-c search_path={schema},public")
    finally:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )

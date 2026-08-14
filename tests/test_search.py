"""Tests for PostgreSQL-backed prose search."""

from types import SimpleNamespace
from typing import Any, Self

import pytest

from backend import db, search
from backend.search import get_source_excerpt, search_umsetzungsleitfaden


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.execute_calls: list[tuple[str, object]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        self.execute_calls.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.cursor_instance = _FakeCursor(rows)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        return None

    def cursor(self, *, row_factory: object = None) -> _FakeCursor:
        return self.cursor_instance


def test_connect_database_uses_configured_postgres_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_connection = object()
    connect_calls: list[str] = []
    monkeypatch.setattr(
        db,
        "load_settings",
        lambda: SimpleNamespace(postgres_uri="postgresql://configured"),
    )

    def fake_connect(database_uri: str) -> object:
        connect_calls.append(database_uri)
        return expected_connection

    monkeypatch.setattr(db.psycopg, "connect", fake_connect)

    assert db.connect_database() is expected_connection
    assert connect_calls == ["postgresql://configured"]


def test_search_returns_ranked_result_models_and_parameterizes_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(
        [
            {
                "source_id": 42,
                "source_type": "umsetzungsleitfaden",
                "title": "Diagnosesicherung",
                "section": "Zulässige Werte",
                "excerpt": "Die Diagnose ist histologisch gesichert.",
                "url": "https://example.test/diagnose",
                "obds_version": None,
                "score": 3.5,
            }
        ]
    )
    monkeypatch.setattr(search, "connect_database", lambda: connection)

    results = search_umsetzungsleitfaden(
        "  Diagnosesicherung  ",
        version=" 3.0.5 ",
        limit=3,
    )

    assert len(results) == 1
    assert results[0].source_id == 42
    assert results[0].score == 3.5
    query, parameters = connection.cursor_instance.execute_calls[0]
    assert "pdb.score(id)" in query
    assert "pdb.boost(3)" in query
    assert "obds_version IS NULL" in query
    assert parameters == (
        "umsetzungsleitfaden",
        "Diagnosesicherung",
        "Diagnosesicherung",
        "Diagnosesicherung",
        "3.0.5",
        "3.0.5",
        3,
    )


@pytest.mark.parametrize(
    ("query", "version", "limit", "message"),
    [
        ("  ", None, 5, "query"),
        ("Diagnose", "  ", 5, "version"),
        ("Diagnose", None, 0, "limit"),
    ],
)
def test_search_rejects_invalid_arguments(
    query: str,
    version: str | None,
    limit: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        search_umsetzungsleitfaden(query, version=version, limit=limit)


def test_get_source_excerpt_returns_complete_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection(
        [
            {
                "source_id": 42,
                "source_type": "umsetzungsleitfaden",
                "title": "Diagnosesicherung",
                "section": None,
                "content": "Vollständiger Quelltext.",
                "url": "https://example.test/diagnose",
                "obds_version": "3.0.5",
            }
        ]
    )
    monkeypatch.setattr(search, "connect_database", lambda: connection)

    result = get_source_excerpt(42)

    assert result is not None
    assert result.content == "Vollständiger Quelltext."
    assert connection.cursor_instance.execute_calls[0][1] == (42,)


def test_get_source_excerpt_returns_none_for_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection([])
    monkeypatch.setattr(search, "connect_database", lambda: connection)

    assert get_source_excerpt(404) is None


def test_get_source_excerpt_rejects_non_positive_id() -> None:
    with pytest.raises(ValueError, match="source_id"):
        get_source_excerpt(0)

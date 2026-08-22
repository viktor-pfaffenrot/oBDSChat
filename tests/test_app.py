"""Tests for the FastAPI backend boundary."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.llm import (
    ConversationTurn,
    QuestionAnswer,
    ToolCallError,
    ToolExecution,
    VersionContext,
)
from backend.xsd import (
    SchemaElement,
    SchemaElementNotFoundError,
    SchemaEvidence,
    SchemaSourceLine,
    SchemaVersionNotFoundError,
)

client = TestClient(app_module.app)


class _Catalog:
    versions = ("3.0.4", "3.0.5")
    latest_version = "3.0.5"

    def __init__(self) -> None:
        self.requested_version: str | None = None

    def resolve_version(self, version: str | None) -> str:
        self.requested_version = version
        assert version is not None
        return version


def test_health_reports_liveness() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_xsd_evidence_returns_exact_source_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = SchemaEvidence(
        element=SchemaElement(
            name="Diagnosesicherung",
            path="/oBDS/Diagnose/Diagnosesicherung",
            datatype="xs:string",
            min_occurs=1,
            max_occurs=1,
            allowed_values=(),
            documentation="Höchste erreichte Diagnosesicherheit",
            version="3.0.5",
            xsd_file="oBDS_v3.0.5.xsd",
            source_url="https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd",
        ),
        source_lines=(
            SchemaSourceLine(
                number=3992,
                content='<xs:element name="Diagnosesicherung">',
                highlighted=True,
            ),
        ),
        declaration_start_line=3992,
        declaration_end_line=4055,
    )

    def fake_get_schema_evidence(path: str, version: str) -> SchemaEvidence:
        assert path == "/oBDS/Diagnose/Diagnosesicherung"
        assert version == "3.0.5"
        return evidence

    monkeypatch.setattr(
        app_module,
        "get_schema_evidence",
        fake_get_schema_evidence,
    )

    response = client.get(
        "/sources/xsd/3.0.5",
        params={"path": "/oBDS/Diagnose/Diagnosesicherung"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "name": "Diagnosesicherung",
        "path": "/oBDS/Diagnose/Diagnosesicherung",
        "datatype": "xs:string",
        "min_occurs": 1,
        "max_occurs": 1,
        "allowed_values": [],
        "documentation": "Höchste erreichte Diagnosesicherheit",
        "version": "3.0.5",
        "xsd_file": "oBDS_v3.0.5.xsd",
        "source_url": "https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd",
        "source_lines": [
            {
                "number": 3992,
                "content": '<xs:element name="Diagnosesicherung">',
                "highlighted": True,
            }
        ],
        "declaration_start_line": 3992,
        "declaration_end_line": 4055,
        "declaration_truncated": False,
    }


def test_xsd_evidence_reports_unknown_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> SchemaEvidence:
        raise SchemaElementNotFoundError("Element path unavailable")

    monkeypatch.setattr(app_module, "get_schema_evidence", fail)

    response = client.get(
        "/sources/xsd/3.0.5",
        params={"path": "/oBDS/Unbekannt"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Element path unavailable"}


def test_query_returns_answer_default_version_and_deduplicated_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _Catalog()
    monkeypatch.setattr(app_module, "get_schema_catalog", lambda: catalog)

    xsd_source = {
        "citation_id": "xsd:3.0.5:/oBDS/Diagnose/Diagnosesicherung",
        "name": "Diagnosesicherung",
        "path": "/oBDS/Diagnose/Diagnosesicherung",
        "version": "3.0.5",
        "xsd_file": "oBDS_v3.0.5.xsd",
        "source_url": "https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd",
        "source_type": "xsd",
    }
    prose_source = {
        "citation_id": "umsetzungsleitfaden:42",
        "source_id": 42,
        "source_type": "umsetzungsleitfaden",
        "title": "Diagnose",
        "section": "Diagnosesicherung",
        "url": "https://example.test/diagnose",
        "obds_version": None,
    }
    irrelevant_source = {
        **prose_source,
        "citation_id": "umsetzungsleitfaden:43",
        "source_id": 43,
        "title": "Nicht verwendeter Treffer",
        "url": "https://example.test/irrelevant",
    }
    answer = QuestionAnswer(
        answer="Belegte Antwort.",
        tool_executions=(
            ToolExecution(
                name="search_schema",
                arguments={"version": "3.0.5"},
                result=[xsd_source, xsd_source],
                output="[]",
            ),
            ToolExecution(
                name="search_umsetzungsleitfaden",
                arguments={},
                result=[prose_source, irrelevant_source],
                output="[]",
            ),
            ToolExecution(
                name="get_source_excerpt",
                arguments={},
                result=prose_source,
                output="{}",
            ),
        ),
        citation_ids=(
            "umsetzungsleitfaden:42",
            "xsd:3.0.5:/oBDS/Diagnose/Diagnosesicherung",
            "umsetzungsleitfaden:42",
        ),
    )

    def fake_answer_question(question: str, **kwargs: object) -> QuestionAnswer:
        assert question == "Welche Werte darf Diagnosesicherung haben?"
        assert kwargs["history"] == ()
        assert kwargs["version_context"] == VersionContext(
            default_version="3.0.5",
            available_versions=("3.0.4", "3.0.5"),
        )
        assert kwargs["tools"] == app_module.TOOLS
        return answer

    monkeypatch.setattr(app_module, "answer_question", fake_answer_question)

    response = client.post(
        "/query",
        json={"question": "Welche Werte darf Diagnosesicherung haben?"},
    )

    assert response.status_code == 200
    assert catalog.requested_version is None
    payload = response.json()
    assert payload["answer"] == "Belegte Antwort."
    assert payload["used_versions"] == ["3.0.5"]
    assert "resolved_version" not in payload
    assert len(payload["sources"]) == 2
    assert payload["sources"][0] == {
        "title": "Diagnose",
        "url": "https://example.test/diagnose",
        "source_type": "umsetzungsleitfaden",
        "section": "Diagnosesicherung",
        "source_id": 42,
    }
    assert payload["sources"][1] == {
        "title": "oBDS_v3.0.5.xsd",
        "url": "https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd",
        "source_type": "xsd",
        "obds_version": "3.0.5",
        "xsd_file": "oBDS_v3.0.5.xsd",
        "element": "Diagnosesicherung",
        "path": "/oBDS/Diagnose/Diagnosesicherung",
    }


def test_query_forwards_normalized_conversation_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "get_schema_catalog", _Catalog)
    answer = QuestionAnswer(answer="Neue Antwort.", tool_executions=())

    def fake_answer_question(question: str, **kwargs: object) -> QuestionAnswer:
        assert question == "Und welche Werte sind dort erlaubt?"
        assert kwargs["history"] == (
            ConversationTurn(
                question="Was bedeutet Diagnosesicherung?",
                answer="Diagnosesicherung beschreibt die diagnostische Grundlage.",
            ),
        )
        return answer

    monkeypatch.setattr(app_module, "answer_question", fake_answer_question)

    response = client.post(
        "/query",
        json={
            "question": "  Und welche Werte sind dort erlaubt?  ",
            "history": [
                {
                    "question": "  Was bedeutet Diagnosesicherung?  ",
                    "answer": (
                        "  Diagnosesicherung beschreibt die diagnostische Grundlage.  "
                    ),
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "Neue Antwort.",
        "used_versions": [],
        "sources": [],
    }


def test_query_forwards_explicit_version(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _Catalog()
    monkeypatch.setattr(app_module, "get_schema_catalog", lambda: catalog)
    answer = QuestionAnswer(
        answer="Antwort.",
        tool_executions=(
            ToolExecution(
                name="search_schema",
                arguments={"version": "3.0.4"},
                result=[],
                output="[]",
            ),
        ),
    )

    def fake_answer_question(question: str, **kwargs: object) -> QuestionAnswer:
        assert kwargs["version_context"] == VersionContext(
            default_version="3.0.5",
            available_versions=("3.0.4", "3.0.5"),
            constraint="3.0.4",
        )
        return answer

    monkeypatch.setattr(app_module, "answer_question", fake_answer_question)

    response = client.post(
        "/query",
        json={"question": "Frage", "obds_version": " 3.0.4 "},
    )

    assert response.status_code == 200
    assert catalog.requested_version == "3.0.4"
    assert response.json()["used_versions"] == ["3.0.4"]


def test_query_reports_multiple_versions_selected_by_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "get_schema_catalog", _Catalog)
    prose_sources = [
        {
            "citation_id": f"umsetzungsleitfaden:{source_id}",
            "source_id": source_id,
            "source_type": "umsetzungsleitfaden",
            "title": "Änderungen",
            "section": "Versionsvergleich",
            "url": "https://example.test/versionsvergleich",
            "obds_version": version,
        }
        for source_id, version in ((44, "3.0.5"), (43, "3.0.4"))
    ]
    answer = QuestionAnswer(
        answer="Vergleich.",
        tool_executions=(
            ToolExecution(
                name="search_umsetzungsleitfaden",
                arguments={"version": "3.0.5"},
                result=[prose_sources[0], prose_sources[0]],
                output="[]",
            ),
            ToolExecution(
                name="search_umsetzungsleitfaden",
                arguments={"version": "3.0.4"},
                result=[prose_sources[1]],
                output="[]",
            ),
            ToolExecution(
                name="search_schema",
                arguments={"version": "9.9.9"},
                result={"error": "unsupported_obds_version"},
                output="{}",
                error="unsupported_obds_version",
            ),
        ),
        citation_ids=("umsetzungsleitfaden:44", "umsetzungsleitfaden:43"),
    )
    monkeypatch.setattr(
        app_module,
        "answer_question",
        lambda *args, **kwargs: answer,
    )

    response = client.post(
        "/query",
        json={
            "question": "Vergleiche die Versionen 3.0.4 und 3.0.5.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["used_versions"] == ["3.0.4", "3.0.5"]
    assert [source["obds_version"] for source in payload["sources"]] == [
        "3.0.5",
        "3.0.4",
    ]


def test_query_rejects_unknown_model_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "get_schema_catalog", _Catalog)
    answer = QuestionAnswer(
        answer="Unbelegte Antwort.",
        tool_executions=(),
        citation_ids=("umsetzungsleitfaden:404",),
    )
    monkeypatch.setattr(
        app_module,
        "answer_question",
        lambda *args, **kwargs: answer,
    )

    response = client.post("/query", json={"question": "Frage"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Language model request failed"}


def test_query_rejects_unknown_version(monkeypatch: pytest.MonkeyPatch) -> None:
    class _MissingCatalog:
        def resolve_version(self, version: str | None) -> str:
            raise SchemaVersionNotFoundError(f"Version {version} is unavailable")

    monkeypatch.setattr(app_module, "get_schema_catalog", _MissingCatalog)

    response = client.post(
        "/query",
        json={"question": "Frage", "obds_version": "9.9.9"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Version 9.9.9 is unavailable"}


@pytest.mark.parametrize(
    ("error_factory", "expected_status", "expected_detail"),
    [
        (
            lambda: ToolCallError("bad tool call"),
            502,
            "Language model request failed",
        ),
        (
            lambda: RuntimeError("missing configuration"),
            503,
            "Backend dependency unavailable",
        ),
    ],
)
def test_query_sanitizes_dependency_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_factory: Callable[[], Exception],
    expected_status: int,
    expected_detail: str,
) -> None:
    monkeypatch.setattr(app_module, "get_schema_catalog", _Catalog)

    def fail(*args: object, **kwargs: object) -> QuestionAnswer:
        raise error_factory()

    monkeypatch.setattr(app_module, "answer_question", fail)

    response = client.post("/query", json={"question": "Frage"})

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}


@pytest.mark.parametrize("question", ["", "   "])
def test_query_rejects_empty_questions(question: str) -> None:
    response = client.post("/query", json={"question": question})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "history",
    [
        [{"question": "", "answer": "Antwort"}],
        [{"question": "Frage", "answer": "   "}],
        [{"question": "Frage", "answer": "Antwort", "role": "assistant"}],
    ],
)
def test_query_rejects_invalid_conversation_turns(
    history: list[dict[str, str]],
) -> None:
    response = client.post(
        "/query",
        json={"question": "Neue Frage", "history": history},
    )

    assert response.status_code == 422


def test_query_rejects_more_than_ten_history_turns() -> None:
    history = [{"question": "Frage", "answer": "Antwort"}] * 11

    response = client.post(
        "/query",
        json={"question": "Neue Frage", "history": history},
    )

    assert response.status_code == 422


def test_query_rejects_history_over_character_limit() -> None:
    history = [{"question": "f" * 2_501, "answer": "a" * 2_500} for _ in range(10)]

    response = client.post(
        "/query",
        json={"question": "Neue Frage", "history": history},
    )

    assert response.status_code == 422

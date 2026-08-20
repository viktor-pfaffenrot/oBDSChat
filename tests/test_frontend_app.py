"""Tests for frontend conversation rendering and XSD viewer routes."""

import pytest
from fastapi.testclient import TestClient

import frontend.app as app_module
from frontend.api import (
    BackendApiError,
    QueryResponse,
    SchemaEnumValue,
    SchemaSourceLine,
    SourceReference,
    XsdEvidence,
)
from frontend.app import CompletedTurn, ConversationState

client = TestClient(app_module.app)


class _BackendClient:
    def __init__(
        self,
        *,
        query_response: QueryResponse | None = None,
        evidence: XsdEvidence | None = None,
    ) -> None:
        self.query_response = query_response
        self.evidence = evidence

    def query(self, question: str, **kwargs: object) -> QueryResponse:
        assert self.query_response is not None
        return self.query_response

    def get_xsd_evidence(self, version: str, path: str) -> XsdEvidence:
        assert version == "3.0.5"
        assert path == "/oBDS/Diagnose/Diagnosesicherung"
        assert self.evidence is not None
        return self.evidence


def _xsd_source() -> SourceReference:
    return SourceReference(
        title="oBDS_v3.0.5.xsd",
        url="https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd",
        source_type="xsd",
        obds_version="3.0.5",
        element="Diagnosesicherung",
        path="/oBDS/Diagnose/Diagnosesicherung",
    )


def _xsd_evidence() -> XsdEvidence:
    return XsdEvidence(
        name="Diagnosesicherung",
        path="/oBDS/Diagnose/Diagnosesicherung",
        datatype="xs:string",
        min_occurs=1,
        max_occurs=1,
        allowed_values=(SchemaEnumValue(value="1", documentation="Klinisch"),),
        documentation="Höchste <script>alert(1)</script> Diagnosesicherheit",
        version="3.0.5",
        xsd_file="oBDS_v3.0.5.xsd",
        source_url="https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd",
        source_lines=(
            SchemaSourceLine(
                number=3992,
                content='<xs:element name="Diagnosesicherung">',
                highlighted=True,
            ),
        ),
        declaration_start_line=3992,
        declaration_end_line=4055,
        declaration_truncated=False,
    )


def test_frontend_health_is_independent_from_backend() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_complete_question_keeps_sources_with_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = QueryResponse(
        answer="Belegte Antwort.",
        used_versions=("3.0.5",),
        sources=(_xsd_source(),),
    )
    monkeypatch.setattr(
        app_module,
        "get_backend_client",
        lambda: _BackendClient(query_response=response),
    )
    pending_state = ConversationState(pending_question="Welche Werte?")

    messages, completed_state = app_module.complete_question(pending_state)

    assert completed_state.pending_question is None
    assert completed_state.turns[0].sources == (_xsd_source(),)
    assert messages[-1].role == "assistant"
    assert "Belegte Antwort." in str(messages[-1].content)
    assert "Feld anzeigen" in str(messages[-1].content)
    assert "/xsd-viewer/3.0.5?path=%2FoBDS%2FDiagnose%2FDiagnosesicherung" in str(
        messages[-1].content
    )


def test_backend_history_keeps_newest_turns_within_character_limit() -> None:
    turns = (
        CompletedTurn(question="alt", answer="a" * 30_000),
        CompletedTurn(question="neu", answer="b" * 30_000),
    )

    history = app_module.build_backend_history(turns)

    assert len(history) == 1
    assert history[0].question == "neu"


def test_xsd_viewer_renders_highlighted_and_escaped_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_module,
        "get_backend_client",
        lambda: _BackendClient(evidence=_xsd_evidence()),
    )

    response = client.get(
        "/xsd-viewer/3.0.5",
        params={"path": "/oBDS/Diagnose/Diagnosesicherung"},
    )

    assert response.status_code == 200
    assert "Exakter Feldnachweis" in response.text
    assert "code-line--target" in response.text
    assert "Zeilen 3992–4055" in response.text
    assert "&lt;xs:element name=&quot;Diagnosesicherung&quot;&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "Höchste &lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_xsd_viewer_preserves_backend_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingBackendClient:
        def get_xsd_evidence(self, version: str, path: str) -> XsdEvidence:
            raise BackendApiError("Element path unavailable", status_code=404)

    monkeypatch.setattr(
        app_module,
        "get_backend_client",
        _FailingBackendClient,
    )

    response = client.get(
        "/xsd-viewer/3.0.5",
        params={"path": "/oBDS/Diagnose/Diagnosesicherung"},
    )

    assert response.status_code == 404
    assert "Feldansicht nicht verfügbar" in response.text
    assert "Element path unavailable" in response.text

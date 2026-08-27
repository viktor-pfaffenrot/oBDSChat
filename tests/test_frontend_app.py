"""Tests for frontend conversation rendering and XSD viewer routes."""

from typing import Literal

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


def test_brand_assets_are_served_and_configured() -> None:
    logo_response = client.get(app_module.LOGO_URL)
    favicon_response = client.get("/favicon.ico")

    assert logo_response.status_code == 200
    assert logo_response.headers["content-type"] == "image/png"
    assert (
        logo_response.content
        == (app_module.ASSETS_PATH / "obdschat-logo-transparent.png").read_bytes()
    )
    assert favicon_response.status_code == 200
    assert favicon_response.content == app_module.FAVICON_PATH.read_bytes()
    assert app_module.interface.favicon_path == str(app_module.FAVICON_PATH)


def test_gradio_masthead_uses_logo() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert app_module.LOGO_URL in response.text
    assert "wordmark__index" not in response.text
    assert "§65c" not in response.text


def test_query_events_allow_bounded_parallel_requests() -> None:
    query_functions = [
        block_function
        for block_function in app_module.interface.fns.values()
        if block_function.fn is app_module.complete_question
    ]

    assert len(query_functions) == 2
    assert app_module.QUERY_CONCURRENCY > 1
    assert {block_function.concurrency_limit for block_function in query_functions} == {
        app_module.QUERY_CONCURRENCY
    }
    assert {block_function.concurrency_id for block_function in query_functions} == {
        "backend-queries"
    }


def test_query_concurrency_defaults_to_eight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(app_module.QUERY_CONCURRENCY_ENV, raising=False)

    assert app_module._load_query_concurrency() == 8


def test_query_concurrency_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(app_module.QUERY_CONCURRENCY_ENV, "12")

    assert app_module._load_query_concurrency() == 12


@pytest.mark.parametrize("configured_value", ["0", "-1", "many"])
def test_query_concurrency_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    configured_value: str,
) -> None:
    monkeypatch.setenv(app_module.QUERY_CONCURRENCY_ENV, configured_value)

    with pytest.raises(RuntimeError, match="must be a positive integer"):
        app_module._load_query_concurrency()


def test_xsd_viewer_links_have_same_tab_navigation_override() -> None:
    assert app_module.interface.head == app_module.SAME_TAB_XSD_LINKS_HEAD
    assert 'a[href^="/xsd-viewer/"]' in app_module.SAME_TAB_XSD_LINKS_HEAD
    assert 'document.createElement("iframe")' in app_module.SAME_TAB_XSD_LINKS_HEAD
    assert "obds-close-xsd-viewer" in app_module.SAME_TAB_XSD_LINKS_HEAD


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
    assistant_content = str(messages[-1].content)
    assert "Belegte Antwort." in assistant_content
    assert (
        '<a href="/xsd-viewer/3.0.5?path=%2FoBDS%2FDiagnose%2FDiagnosesicherung">'
        "Feld anzeigen</a>"
    ) in assistant_content
    assert (
        'target="_blank" rel="noopener noreferrer">Offizielle XSD</a>'
        in assistant_content
    )


def test_backend_history_keeps_newest_turns_within_character_limit() -> None:
    turns = (
        CompletedTurn(question="alt", answer="a" * 30_000),
        CompletedTurn(question="neu", answer="b" * 30_000),
    )

    history = app_module.build_backend_history(turns)

    assert len(history) == 1
    assert history[0].question == "neu"


def test_clipboard_transcript_has_roles_and_readable_sources() -> None:
    state = ConversationState(
        turns=(
            CompletedTurn(
                question="Ist Zentrumsfall ein Pflichtfeld?",
                answer="Nein, das Feld ist optional.",
                sources=(_xsd_source(),),
            ),
        ),
        pending_question="Wie sieht es mit Diagnosesicherung aus?",
    )

    transcript = app_module.format_conversation_for_clipboard(state)

    assert transcript == (
        "User: Ist Zentrumsfall ein Pflichtfeld?\n\n"
        "Chatbot: Nein, das Feld ist optional.\n\n"
        "Quellen:\n"
        "- oBDS_v3.0.5.xsd\n"
        "  Typ: XSD\n"
        "  Version: 3.0.5\n"
        "  XML-Pfad: /oBDS/Diagnose/Diagnosesicherung\n"
        "  URL: https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd\n\n"
        "User: Wie sieht es mit Diagnosesicherung aus?"
    )
    assert "<details" not in transcript


def test_clipboard_transcript_is_empty_without_messages() -> None:
    assert app_module.format_conversation_for_clipboard(None) == ""


@pytest.mark.parametrize(
    ("min_occurs", "max_occurs", "expected"),
    (
        (0, 1, "Optional, höchstens einmal"),
        (1, 1, "Pflichtfeld, genau einmal"),
        (0, "unbounded", "Optional, mehrfach möglich"),
        (1, "unbounded", "Pflichtfeld, mehrfach möglich"),
    ),
)
def test_occurrence_label_translates_common_values(
    min_occurs: int,
    max_occurs: int | Literal["unbounded"],
    expected: str,
) -> None:
    assert app_module._occurrence_label(min_occurs, max_occurs) == expected


def test_source_markup_removes_only_shared_indentation() -> None:
    evidence = _xsd_evidence().model_copy(
        update={
            "source_lines": (
                SchemaSourceLine(
                    number=3992,
                    content='        <xs:element name="Diagnosesicherung">',
                    highlighted=True,
                ),
                SchemaSourceLine(
                    number=3993,
                    content="          <xs:annotation>",
                    highlighted=False,
                ),
            )
        }
    )

    markup = app_module._source_markup(evidence)

    assert 'code-line__content">&lt;xs:element' in markup
    assert 'code-line__content">  &lt;xs:annotation&gt;' in markup


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
    assert '<h2 class="medium-heading">Diagnosesicherung</h2>' in response.text
    assert "<dt>Vorkommen</dt><dd>Pflichtfeld, genau einmal</dd>" in response.text
    assert "code-line--target" in response.text
    assert "Zeilen 3992–4055" in response.text
    assert "obds-close-xsd-viewer" in response.text
    assert "history.back()" in response.text
    assert app_module.LOGO_URL in response.text
    assert "wordmark__index" not in response.text
    assert "§65c" not in response.text
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
    assert "obds-close-xsd-viewer" in response.text
    assert "history.back()" in response.text

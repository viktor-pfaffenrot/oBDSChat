"""Tests for the frontend's typed backend HTTP client."""

import json

import httpx
import pytest

from frontend.api import (
    BackendApiError,
    BackendClient,
    ConversationTurn,
)


def test_query_sends_history_and_validates_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/query"
        assert json.loads(request.content) == {
            "question": "Und welche Werte?",
            "history": [
                {
                    "question": "Was ist Diagnosesicherung?",
                    "answer": "Ein oBDS-Feld.",
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "answer": "Die Werte sind …",
                "used_versions": ["3.0.5"],
                "sources": [
                    {
                        "title": "oBDS_v3.0.5.xsd",
                        "url": ("https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd"),
                        "source_type": "xsd",
                        "obds_version": "3.0.5",
                        "element": "Diagnosesicherung",
                        "path": "/oBDS/Diagnose/Diagnosesicherung",
                    }
                ],
            },
        )

    client = BackendClient(
        "https://backend.test",
        transport=httpx.MockTransport(handler),
    )

    response = client.query(
        "  Und welche Werte?  ",
        history=(
            ConversationTurn(
                question="Was ist Diagnosesicherung?",
                answer="Ein oBDS-Feld.",
            ),
        ),
    )

    assert response.answer == "Die Werte sind …"
    assert response.used_versions == ("3.0.5",)
    assert response.sources[0].element == "Diagnosesicherung"


def test_get_xsd_evidence_encodes_path_as_query_parameter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sources/xsd/3.0.5"
        assert request.url.params["path"] == "/oBDS/Diagnose/Diagnosesicherung"
        return httpx.Response(
            200,
            json={
                "name": "Diagnosesicherung",
                "path": "/oBDS/Diagnose/Diagnosesicherung",
                "datatype": "xs:string",
                "min_occurs": 1,
                "max_occurs": 1,
                "allowed_values": [{"value": "1"}],
                "version": "3.0.5",
                "xsd_file": "oBDS_v3.0.5.xsd",
                "source_url": ("https://www.basisdatensatz.de/xml/oBDS_v3.0.5.xsd"),
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
            },
        )

    client = BackendClient(
        "https://backend.test",
        transport=httpx.MockTransport(handler),
    )

    evidence = client.get_xsd_evidence(
        "3.0.5",
        "/oBDS/Diagnose/Diagnosesicherung",
    )

    assert evidence.name == "Diagnosesicherung"
    assert evidence.source_lines[0].number == 3992


def test_client_exposes_backend_error_detail() -> None:
    client = BackendClient(
        "https://backend.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                404,
                json={"detail": "Element path unavailable"},
            )
        ),
    )

    with pytest.raises(BackendApiError, match="Element path unavailable") as result:
        client.get_xsd_evidence("3.0.5", "/oBDS/Unbekannt")

    assert result.value.status_code == 404


def test_client_sanitizes_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = BackendClient(
        "https://backend.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(BackendApiError, match="nicht erreichbar"):
        client.query("Frage")


def test_client_rejects_invalid_backend_url() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        BackendClient("backend:8000")

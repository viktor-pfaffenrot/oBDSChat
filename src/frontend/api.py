"""Typed HTTP boundary between the Gradio frontend and FastAPI backend."""

import os
from collections.abc import Sequence
from typing import Final, Literal
from urllib.parse import quote

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)

BACKEND_URL_ENV: Final = "BACKEND_URL"
DEFAULT_BACKEND_URL: Final = "http://localhost:8000"
REQUEST_TIMEOUT: Final = httpx.Timeout(120.0, connect=5.0)


class ConversationTurn(BaseModel):
    """One completed frontend conversation turn sent as backend context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=10_000)
    answer: str = Field(min_length=1, max_length=50_000)

    @field_validator("question", "answer", mode="after")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("must not be empty")
        return normalized_value


class SourceReference(BaseModel):
    """Citation metadata returned with one backend answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    url: HttpUrl
    source_type: str
    section: str | None = None
    obds_version: str | None = None
    source_id: int | None = Field(default=None, gt=0)
    xsd_file: str | None = None
    element: str | None = None
    path: str | None = None


class QueryResponse(BaseModel):
    """Validated answer returned by the backend query endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    used_versions: tuple[str, ...]
    sources: tuple[SourceReference, ...]


class SchemaEnumValue(BaseModel):
    """One allowed XSD value shown in the evidence viewer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str
    documentation: str | None = None


class SchemaSourceLine(BaseModel):
    """One numbered source line returned by the evidence endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int = Field(gt=0)
    content: str
    highlighted: bool


class XsdEvidence(BaseModel):
    """Exact XSD field evidence rendered by the frontend viewer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    path: str
    datatype: str
    base_datatype: str | None = None
    min_occurs: int = Field(ge=0)
    max_occurs: int | Literal["unbounded"]
    allowed_values: tuple[SchemaEnumValue, ...]
    documentation: str | None = None
    datatype_documentation: str | None = None
    version: str
    xsd_file: str
    source_url: HttpUrl
    source_lines: tuple[SchemaSourceLine, ...]
    declaration_start_line: int | None = Field(default=None, gt=0)
    declaration_end_line: int | None = Field(default=None, gt=0)
    declaration_truncated: bool


class _QueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=10_000)
    obds_version: str | None = Field(default=None, max_length=50)
    history: tuple[ConversationTurn, ...] = Field(default=(), max_length=10)

    @field_validator("question", "obds_version", mode="after")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("must not be empty")
        return normalized_value


class BackendApiError(RuntimeError):
    """Frontend-safe failure raised for transport or backend response errors."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BackendClient:
    """Small synchronous client for the frontend's backend-only HTTP calls."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/")
        parsed_url = httpx.URL(normalized_url)
        if parsed_url.scheme not in {"http", "https"} or parsed_url.host is None:
            raise ValueError("BACKEND_URL must be an absolute HTTP(S) URL")
        self.base_url = normalized_url
        self._transport = transport

    @classmethod
    def from_environment(cls) -> "BackendClient":
        """Build a client from BACKEND_URL, with a local development default."""
        return cls(os.environ.get(BACKEND_URL_ENV, DEFAULT_BACKEND_URL))

    def query(
        self,
        question: str,
        *,
        history: Sequence[ConversationTurn] = (),
        obds_version: str | None = None,
    ) -> QueryResponse:
        """Submit one complete question and return its complete final answer."""
        payload = _QueryPayload(
            question=question,
            obds_version=obds_version,
            history=tuple(history),
        )
        response = self._request(
            "POST",
            "/query",
            json=payload.model_dump(mode="json", exclude_none=True),
        )
        return _validate_response(response, QueryResponse)

    def get_xsd_evidence(self, version: str, path: str) -> XsdEvidence:
        """Fetch exact source evidence for one versioned XML path."""
        normalized_version = version.strip()
        normalized_path = path.strip()
        if not normalized_version:
            raise ValueError("version must not be empty")
        if not normalized_path:
            raise ValueError("path must not be empty")
        response = self._request(
            "GET",
            f"/sources/xsd/{quote(normalized_version, safe='')}",
            params={"path": normalized_path},
        )
        return _validate_response(response, XsdEvidence)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=REQUEST_TIMEOUT,
                transport=self._transport,
            ) as client:
                response = client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                )
        except httpx.RequestError as error:
            raise BackendApiError("Backend ist derzeit nicht erreichbar.") from error

        if response.is_error:
            raise BackendApiError(
                _error_detail(response),
                status_code=response.status_code,
            )
        return response


def _validate_response[ResponseModel: BaseModel](
    response: httpx.Response,
    model: type[ResponseModel],
) -> ResponseModel:
    try:
        return model.model_validate(response.json())
    except ValueError as error:
        raise BackendApiError(
            "Backend hat eine ungültige Antwort geliefert."
        ) from error


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return f"Backend-Anfrage fehlgeschlagen ({response.status_code})."

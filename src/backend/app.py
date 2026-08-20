"""FastAPI application for source-grounded oBDS questions."""

from collections.abc import Iterator, Mapping
from typing import Final, Literal, Self

from fastapi import FastAPI, HTTPException, status
from openai import OpenAIError
from psycopg import Error as PsycopgError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from backend.llm import (
    ConversationTurn,
    QuestionAnswer,
    ToolCallError,
    ToolExecution,
    VersionContext,
    answer_question,
)
from backend.tools import CITATION_ID_FIELD, TOOLS
from backend.xsd import (
    SchemaError,
    SchemaVersionNotFoundError,
    get_schema_catalog,
)

MAX_HISTORY_TURNS: Final = 10
MAX_HISTORY_CHARACTERS: Final = 50_000


class HealthResponse(BaseModel):
    """Backend liveness response."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"


class ConversationTurnRequest(BaseModel):
    """One completed question-answer turn supplied as conversation context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=10_000)
    answer: str = Field(min_length=1, max_length=MAX_HISTORY_CHARACTERS)

    @field_validator("question", "answer", mode="after")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        """Trim turn content and reject whitespace-only values."""
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("must not be empty")
        return normalized_value


class QueryRequest(BaseModel):
    """Validated oBDS question submitted by an HTTP client."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=10_000)
    obds_version: str | None = Field(default=None, max_length=50)
    history: tuple[ConversationTurnRequest, ...] = Field(
        default=(),
        max_length=MAX_HISTORY_TURNS,
    )

    @field_validator("question", "obds_version", mode="after")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        """Trim request text and reject whitespace-only values."""
        if value is None:
            return None
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("must not be empty")
        return normalized_value

    @model_validator(mode="after")
    def validate_history_size(self) -> Self:
        """Limit total context size independently of turn count."""
        character_count = sum(
            len(turn.question) + len(turn.answer) for turn in self.history
        )
        if character_count > MAX_HISTORY_CHARACTERS:
            raise ValueError(
                f"history must not exceed {MAX_HISTORY_CHARACTERS} characters"
            )
        return self


class SourceReference(BaseModel):
    """Public citation metadata derived from one local tool result."""

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
    """LLM answer with used schema versions and deduplicated evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    used_versions: tuple[str, ...]
    sources: tuple[SourceReference, ...]


app = FastAPI(title="oBDSChat Backend")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report process liveness without calling external dependencies."""
    return HealthResponse()


@app.post(
    "/query",
    response_model=QueryResponse,
    response_model_exclude_none=True,
)
def query_obds(request: QueryRequest) -> QueryResponse:
    """Answer one oBDS question using the configured model and local tools."""
    try:
        version_context = _build_version_context(request.obds_version)
        result = answer_question(
            request.question,
            history=_build_conversation_history(request.history),
            version_context=version_context,
            tools=TOOLS,
        )
        return _build_query_response(result, version_context)
    except SchemaVersionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (OpenAIError, ToolCallError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Language model request failed",
        ) from error
    except (PsycopgError, SchemaError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backend dependency unavailable",
        ) from error


def _build_conversation_history(
    history: tuple[ConversationTurnRequest, ...],
) -> tuple[ConversationTurn, ...]:
    return tuple(
        ConversationTurn(question=turn.question, answer=turn.answer) for turn in history
    )


def _build_version_context(requested_version: str | None) -> VersionContext:
    catalog = get_schema_catalog()
    constraint = None
    if requested_version is not None:
        constraint = catalog.resolve_version(requested_version)
    return VersionContext(
        default_version=catalog.latest_version,
        available_versions=catalog.versions,
        constraint=constraint,
    )


def _build_query_response(
    result: QuestionAnswer,
    version_context: VersionContext,
) -> QueryResponse:
    sources = _collect_sources(result.tool_executions, result.citation_ids)
    return QueryResponse(
        answer=result.answer,
        used_versions=_collect_used_versions(
            result.tool_executions,
            sources,
            version_context.available_versions,
        ),
        sources=sources,
    )


def _collect_used_versions(
    tool_executions: tuple[ToolExecution, ...],
    sources: tuple[SourceReference, ...],
    available_versions: tuple[str, ...],
) -> tuple[str, ...]:
    used_versions: set[str] = set()
    for execution in tool_executions:
        version = execution.arguments.get("version")
        if execution.error is None and isinstance(version, str):
            used_versions.add(version)
    for source in sources:
        if source.obds_version is not None:
            used_versions.add(source.obds_version)

    ordered_versions = [
        version for version in available_versions if version in used_versions
    ]
    ordered_versions.extend(sorted(used_versions.difference(available_versions)))
    return tuple(ordered_versions)


def _collect_sources(
    tool_executions: tuple[ToolExecution, ...],
    citation_ids: tuple[str, ...],
) -> tuple[SourceReference, ...]:
    sources_by_citation_id: dict[str, SourceReference] = {}
    for execution in tool_executions:
        for item in _iter_source_items(execution.result):
            citation_id = _string_value(item, CITATION_ID_FIELD)
            source = _source_from_item(item)
            if citation_id is not None and source is not None:
                sources_by_citation_id.setdefault(citation_id, source)

    selected_sources: list[SourceReference] = []
    seen_keys: set[tuple[str, str, str, str, str]] = set()
    for citation_id in citation_ids:
        source = sources_by_citation_id.get(citation_id)
        if source is None:
            raise ToolCallError(f"Model cited unknown evidence: {citation_id}")
        key = _source_key(source)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected_sources.append(source)
    return tuple(selected_sources)


def _iter_source_items(result: object) -> Iterator[Mapping[str, object]]:
    if isinstance(result, Mapping):
        yield result
        return
    if isinstance(result, (list, tuple)):
        for item in result:
            yield from _iter_source_items(item)


def _source_from_item(item: Mapping[str, object]) -> SourceReference | None:
    source_type = _string_value(item, "source_type")
    url = _string_value(item, "url") or _string_value(item, "source_url")
    if source_type is None or url is None:
        return None

    xsd_file = _string_value(item, "xsd_file")
    element = _string_value(item, "name")
    title = _string_value(item, "title") or xsd_file or element
    if title is None:
        return None

    source_id = item.get("source_id")
    if not isinstance(source_id, int):
        source_id = None

    return SourceReference(
        title=title,
        url=url,
        source_type=source_type,
        section=_string_value(item, "section"),
        obds_version=(
            _string_value(item, "obds_version") or _string_value(item, "version")
        ),
        source_id=source_id,
        xsd_file=xsd_file,
        element=element,
        path=_string_value(item, "path"),
    )


def _source_key(source: SourceReference) -> tuple[str, str, str, str, str]:
    return (
        source.source_type,
        str(source.url),
        source.obds_version or "",
        source.section or "",
        source.path or "",
    )


def _string_value(item: Mapping[str, object], key: str) -> str | None:
    value = item.get(key)
    if not isinstance(value, str):
        return None
    normalized_value = value.strip()
    return normalized_value or None

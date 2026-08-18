from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call

import pytest
from openai import OpenAI

from backend.config import (
    OPENAI_BASE_URL,
    REQUESTY_BASE_URL,
    REQUESTY_POLICY_ROUTE,
    LlmEndpoint,
    LlmProvider,
)
from backend.llm import (
    LocalTool,
    ModelAnswer,
    ToolCallError,
    VersionContext,
    answer_question,
    create_client,
)


class FakeResponses:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return next(self._responses)


class FakeOpenAI:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = FakeResponses(responses)


def _endpoint(route: str = "gpt-5.6-terra") -> LlmEndpoint:
    return LlmEndpoint(
        provider=LlmProvider.OPENAI,
        base_url=OPENAI_BASE_URL,
        route=route,
        api_key="test-key",
    )


def _schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }


def _versioned_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "version": {"type": ["string", "null"]},
        },
        "required": ["query", "version"],
        "additionalProperties": False,
    }


def _version_context(constraint: str | None = None) -> VersionContext:
    return VersionContext(
        default_version="3.0.5",
        available_versions=("3.0.4", "3.0.5"),
        constraint=constraint,
    )


def _function_call(
    name: str,
    arguments: str,
    call_id: str = "call_1",
) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=arguments,
        call_id=call_id,
    )


def _tool_response(
    *output_items: SimpleNamespace,
) -> SimpleNamespace:
    return SimpleNamespace(
        output=list(output_items),
        output_parsed=None,
    )


def _final_response(
    answer: str,
    citation_ids: tuple[str, ...] = (),
    *,
    supported: bool | None = None,
) -> SimpleNamespace:
    if supported is None:
        supported = bool(citation_ids)
    return SimpleNamespace(
        output=[],
        output_parsed=SimpleNamespace(
            answer=answer,
            supported=supported,
            citation_ids=citation_ids,
        ),
    )


def test_create_client_uses_resolved_endpoint() -> None:
    endpoint = LlmEndpoint(
        provider=LlmProvider.REQUESTY,
        base_url=REQUESTY_BASE_URL,
        route=REQUESTY_POLICY_ROUTE,
        api_key="requesty-key",
    )

    client = create_client(endpoint)

    assert str(client.base_url) == f"{REQUESTY_BASE_URL}/"
    assert client.api_key == "requesty-key"
    client.close()


def test_answer_question_uses_configured_route() -> None:
    fake_client = FakeOpenAI([_final_response("Diagnosesicherung erklärt.")])

    result = answer_question(
        "Was bedeutet Diagnosesicherung?",
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    request = fake_client.responses.calls[0]
    assert result.answer == "Diagnosesicherung erklärt."
    assert result.tool_executions == ()
    assert request["model"] == "gpt-5.6-terra"
    assert request["text_format"] is ModelAnswer
    assert request["input"][0] == {
        "role": "user",
        "content": "Was bedeutet Diagnosesicherung?",
    }
    assert request["store"] is False
    assert "reasoning" not in request
    assert "reasoning_effort" not in request


def test_answer_question_executes_function_call() -> None:
    reasoning_item = SimpleNamespace(type="reasoning")
    function_call = _function_call(
        "search_schema",
        '{"query":"Diagnosesicherung"}',
    )
    fake_client = FakeOpenAI(
        [
            _tool_response(reasoning_item, function_call),
            _final_response(
                "Belegte Antwort.",
                (" source:1 ", "source:1"),
            ),
        ]
    )
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_schema(),
        handler=lambda query: {"element": query},
    )

    result = answer_question(
        "Welche Werte sind erlaubt?",
        tools=[tool],
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    assert result.answer == "Belegte Antwort."
    assert result.citation_ids == ("source:1",)
    assert len(result.tool_executions) == 1
    assert result.tool_executions[0].name == "search_schema"
    assert result.tool_executions[0].result == {"element": "Diagnosesicherung"}
    first_request = fake_client.responses.calls[0]
    second_request = fake_client.responses.calls[1]
    definition = first_request["tools"][0]
    assert definition["strict"] is True
    assert "reasoning" not in first_request
    assert "reasoning" not in second_request
    assert first_request["tool_choice"] == "required"
    assert second_request["tool_choice"] == "auto"
    next_input = second_request["input"]
    assert reasoning_item in next_input
    assert {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"element": "Diagnosesicherung"}',
    } in next_input


def test_answer_question_enforces_version_constraint_on_tool_calls() -> None:
    function_call = _function_call(
        "search_schema",
        '{"query":"Diagnose","version":"3.0.0"}',
    )
    fake_client = FakeOpenAI(
        [
            _tool_response(function_call),
            _final_response("Antwort für 3.0.5."),
        ]
    )
    handler = Mock(return_value=[])
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_versioned_schema(),
        handler=handler,
    )

    result = answer_question(
        "Welche Werte sind erlaubt?",
        version_context=_version_context(constraint="3.0.5"),
        tools=[tool],
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    handler.assert_called_once_with(query="Diagnose", version="3.0.5")
    assert result.tool_executions[0].arguments["version"] == "3.0.5"
    instructions = fake_client.responses.calls[0]["instructions"]
    assert "Use only oBDS version 3.0.5" in instructions


def test_answer_question_applies_default_version_to_null_tool_argument() -> None:
    function_call = _function_call(
        "search_schema",
        '{"query":"Diagnose","version":null}',
    )
    fake_client = FakeOpenAI(
        [
            _tool_response(function_call),
            _final_response("Antwort für 3.0.5."),
        ]
    )
    handler = Mock(return_value=[])
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_versioned_schema(),
        handler=handler,
    )

    result = answer_question(
        "Welche Werte sind erlaubt?",
        version_context=_version_context(),
        tools=[tool],
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    handler.assert_called_once_with(query="Diagnose", version="3.0.5")
    assert result.tool_executions[0].arguments["version"] == "3.0.5"


def test_answer_question_preserves_versions_for_comparison() -> None:
    calls = [
        _function_call(
            "search_schema",
            f'{{"query":"Änderungen","version":"{version}"}}',
            call_id=f"call_{version}",
        )
        for version in ("3.0.4", "3.0.5")
    ]
    fake_client = FakeOpenAI(
        [
            _tool_response(*calls),
            _final_response("Vergleich."),
        ]
    )
    handler = Mock(return_value=[])
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_versioned_schema(),
        handler=handler,
    )

    result = answer_question(
        "Vergleiche 3.0.4 und 3.0.5.",
        version_context=_version_context(),
        tools=[tool],
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    assert handler.call_args_list == [
        call(query="Änderungen", version="3.0.4"),
        call(query="Änderungen", version="3.0.5"),
    ]
    assert [execution.arguments["version"] for execution in result.tool_executions] == [
        "3.0.4",
        "3.0.5",
    ]


def test_answer_question_returns_unsupported_version_to_model() -> None:
    function_call = _function_call(
        "search_schema",
        '{"query":"Diagnose","version":"9.9.9"}',
    )
    fake_client = FakeOpenAI(
        [
            _tool_response(function_call),
            _final_response("Version nicht verfügbar."),
        ]
    )
    handler = Mock(return_value=[])
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_versioned_schema(),
        handler=handler,
    )

    result = answer_question(
        "Was gilt in Version 9.9.9?",
        version_context=_version_context(),
        tools=[tool],
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    handler.assert_not_called()
    execution = result.tool_executions[0]
    assert execution.error == "unsupported_obds_version"
    assert execution.result == {
        "error": "unsupported_obds_version",
        "message": "oBDS version 9.9.9 is unavailable",
        "requested_version": "9.9.9",
        "available_versions": ["3.0.4", "3.0.5"],
    }
    assert fake_client.responses.calls[1]["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": execution.output,
    }


def test_answer_question_rejects_unknown_tool_call() -> None:
    function_call = _function_call("unknown", "{}")
    fake_client = FakeOpenAI([_tool_response(function_call)])

    with pytest.raises(ToolCallError, match="Unknown tool"):
        answer_question(
            "Test",
            client=cast(OpenAI, fake_client),
            endpoint=_endpoint(),
        )


def test_answer_question_rejects_supported_answer_without_citations() -> None:
    fake_client = FakeOpenAI([_final_response("Unbelegte Antwort.", supported=True)])

    with pytest.raises(ToolCallError, match="without citations"):
        answer_question(
            "Test",
            client=cast(OpenAI, fake_client),
            endpoint=_endpoint(),
        )

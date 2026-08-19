import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call

import httpx
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


class FakeChatCompletions:
    def __init__(self, completions: list[SimpleNamespace]) -> None:
        self._completions = iter(completions)
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(deepcopy(kwargs))
        return next(self._completions)


class FakeChat:
    def __init__(self, completions: list[SimpleNamespace]) -> None:
        self.completions = FakeChatCompletions(completions)


class FakeOpenAI:
    def __init__(self, completions: list[SimpleNamespace]) -> None:
        self.chat = FakeChat(completions)


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
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name=name,
            arguments=arguments,
        ),
    )


def _tool_completion(
    *function_calls: SimpleNamespace,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    role="assistant",
                    content=None,
                    parsed=None,
                    tool_calls=list(function_calls),
                )
            )
        ]
    )


def _final_completion(
    answer: str,
    citation_ids: tuple[str, ...] = (),
    *,
    supported: bool | None = None,
) -> SimpleNamespace:
    if supported is None:
        supported = bool(citation_ids)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    role="assistant",
                    content=answer,
                    parsed=SimpleNamespace(
                        answer=answer,
                        supported=supported,
                        citation_ids=citation_ids,
                    ),
                    tool_calls=None,
                )
            )
        ]
    )


def _api_completion(
    message: dict[str, object],
    *,
    finish_reason: str,
    completion_id: str,
) -> dict[str, object]:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": message,
            }
        ],
    }


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


def test_answer_question_uses_chat_completions_wire_protocol() -> None:
    requests: list[dict[str, Any]] = []
    request_paths: list[str] = []
    responses = iter(
        [
            _api_completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "search_schema",
                                "arguments": '{"query":"Diagnose"}',
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
                completion_id="chatcmpl_tools",
            ),
            _api_completion(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "answer": "Belegte Antwort.",
                            "supported": False,
                            "citation_ids": [],
                        }
                    ),
                },
                finish_reason="stop",
                completion_id="chatcmpl_answer",
            ),
        ]
    )

    def handle_request(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(responses))

    http_client = httpx.Client(transport=httpx.MockTransport(handle_request))
    client = OpenAI(
        api_key="test-key",
        base_url="https://example.test/v1",
        http_client=http_client,
    )
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_schema(),
        handler=lambda query: {"element": query},
    )

    try:
        result = answer_question(
            "Was bedeutet Diagnose?",
            tools=[tool],
            client=client,
            endpoint=_endpoint("test-model"),
        )
    finally:
        client.close()

    assert result.answer == "Belegte Antwort."
    assert request_paths == ["/v1/chat/completions", "/v1/chat/completions"]
    assert len(requests) == 2
    first_request, second_request = requests
    assert first_request["response_format"]["type"] == "json_schema"
    assert first_request["tools"][0]["function"]["strict"] is True
    assistant_message = second_request["messages"][2]
    assert "parsed" not in assistant_message
    assert "parsed_arguments" not in assistant_message["tool_calls"][0]["function"]
    assert second_request["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"element": "Diagnose"}',
    }


def test_answer_question_uses_configured_route() -> None:
    fake_client = FakeOpenAI([_final_completion("Diagnosesicherung erklärt.")])

    result = answer_question(
        "Was bedeutet Diagnosesicherung?",
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    request = fake_client.chat.completions.calls[0]
    assert result.answer == "Diagnosesicherung erklärt."
    assert result.tool_executions == ()
    assert request["model"] == "gpt-5.6-terra"
    assert request["response_format"] is ModelAnswer
    assert request["messages"][1] == {
        "role": "user",
        "content": "Was bedeutet Diagnosesicherung?",
    }
    assert request["messages"][0]["role"] == "system"
    assert "Beantworte Fragen zum deutschen oBDS" in request["messages"][0]["content"]
    assert (
        "beschreibe oder verspreche niemals einen zukünftigen Werkzeugaufruf"
        in request["messages"][0]["content"]
    )
    assert (
        "Wiederhole niemals einen identischen Werkzeugaufruf"
        in request["messages"][0]["content"]
    )
    assert (
        "search_schema liefert eine begrenzte Trefferliste"
        in request["messages"][0]["content"]
    )
    assert "get_schema_element mit diesem Namen" in request["messages"][0]["content"]
    assert (
        "die erfragten Daten direkt repräsentieren" in request["messages"][0]["content"]
    )
    assert "Beantworte nur die gestellte Frage" in request["messages"][0]["content"]
    assert request["store"] is False
    assert "reasoning" not in request
    assert "reasoning_effort" not in request


def test_answer_question_executes_function_call() -> None:
    function_call = _function_call(
        "search_schema",
        '{"query":"Diagnosesicherung"}',
    )
    tool_completion = _tool_completion(function_call)
    fake_client = FakeOpenAI(
        [
            tool_completion,
            _final_completion(
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
    first_request = fake_client.chat.completions.calls[0]
    second_request = fake_client.chat.completions.calls[1]
    definition = first_request["tools"][0]
    assert definition["function"]["strict"] is True
    assert "reasoning" not in first_request
    assert "reasoning" not in second_request
    assert first_request["tool_choice"] == "required"
    assert second_request["tool_choice"] == "auto"
    messages = second_request["messages"]
    assert messages[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "search_schema",
                    "arguments": '{"query":"Diagnosesicherung"}',
                },
            }
        ],
    }
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"element": "Diagnosesicherung"}',
    }


def test_answer_question_supports_dependent_tool_rounds() -> None:
    first_call = _function_call(
        "search_schema",
        '{"query":"Diagnose"}',
        call_id="call_1",
    )
    second_call = _function_call(
        "search_schema",
        '{"query":"Diagnosesicherung"}',
        call_id="call_2",
    )
    fake_client = FakeOpenAI(
        [
            _tool_completion(first_call),
            _tool_completion(second_call),
            _final_completion("Synthese."),
        ]
    )
    handler = Mock(side_effect=[{"match": "Diagnose"}, {"match": "Sicherung"}])
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_schema(),
        handler=handler,
    )

    result = answer_question(
        "Was bedeutet Diagnosesicherung?",
        tools=[tool],
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    assert result.answer == "Synthese."
    assert handler.call_args_list == [
        call(query="Diagnose"),
        call(query="Diagnosesicherung"),
    ]
    assert len(fake_client.chat.completions.calls) == 3
    final_messages = fake_client.chat.completions.calls[2]["messages"]
    assert len(final_messages) == 6
    assert final_messages[0]["role"] == "system"
    assert final_messages[1]["role"] == "user"
    assert final_messages[2]["role"] == "assistant"
    assert final_messages[3]["role"] == "tool"
    assert final_messages[4]["role"] == "assistant"
    assert final_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call_2",
        "content": '{"match": "Sicherung"}',
    }


def test_answer_question_requires_configured_follow_up_before_final_answer() -> None:
    search_handler = Mock(
        return_value=[{"citation_id": "xsd:diagnose", "path": "/Diagnose"}]
    )
    exact_handler = Mock(
        return_value=[
            {"citation_id": "xsd:diagnose", "path": "/Diagnose"},
            {"citation_id": "xsd:pathologie", "path": "/Pathologie"},
        ]
    )
    values_handler = Mock(return_value=[])
    fake_client = FakeOpenAI(
        [
            _tool_completion(
                _function_call(
                    "search_schema",
                    '{"query":"genetische Marker"}',
                    call_id="call_1",
                )
            ),
            _final_completion("Nur Diagnose.", ("xsd:diagnose",)),
            _tool_completion(
                _function_call(
                    "get_schema_element",
                    '{"query":"Genetische_Variante"}',
                    call_id="call_2",
                )
            ),
            _final_completion(
                "Diagnose und Pathologie.",
                ("xsd:diagnose", "xsd:pathologie"),
            ),
        ]
    )
    search_tool = LocalTool(
        name="search_schema",
        description="Find candidate schema elements.",
        parameters=_schema(),
        handler=search_handler,
        follow_up_tools=("get_schema_element", "get_schema_values"),
    )
    exact_tool = LocalTool(
        name="get_schema_element",
        description="Get all paths for an exact element name.",
        parameters=_schema(),
        handler=exact_handler,
    )
    values_tool = LocalTool(
        name="get_schema_values",
        description="Get values for an exact element name.",
        parameters=_schema(),
        handler=values_handler,
    )

    result = answer_question(
        "In welchen Meldungstypen kommen genetische Marker vor?",
        tools=[search_tool, exact_tool, values_tool],
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    assert result.answer == "Diagnose und Pathologie."
    assert [execution.name for execution in result.tool_executions] == [
        "search_schema",
        "get_schema_element",
    ]
    assert [
        request["tool_choice"] for request in fake_client.chat.completions.calls
    ] == ["required", "auto", "required", "auto"]
    follow_up_definitions = fake_client.chat.completions.calls[2]["tools"]
    assert [tool["function"]["name"] for tool in follow_up_definitions] == [
        "get_schema_element",
        "get_schema_values",
    ]
    search_handler.assert_called_once_with(query="genetische Marker")
    exact_handler.assert_called_once_with(query="Genetische_Variante")
    values_handler.assert_not_called()


def test_answer_question_rejects_unknown_configured_follow_up_tool() -> None:
    tool = LocalTool(
        name="search_schema",
        description="Find candidate schema elements.",
        parameters=_schema(),
        handler=lambda query: [],
        follow_up_tools=("missing_tool",),
    )

    with pytest.raises(ValueError, match="unknown follow-up tools: missing_tool"):
        answer_question(
            "Test",
            tools=[tool],
            client=cast(OpenAI, FakeOpenAI([])),
            endpoint=_endpoint(),
        )


@pytest.mark.parametrize(
    "premature_answer",
    [
        "Dafür müsste der Umsetzungsleitfaden durchsucht werden.",
        "",
    ],
)
def test_answer_question_recovers_from_premature_unsupported_answer(
    premature_answer: str,
) -> None:
    schema_handler = Mock(return_value=[])
    exact_handler = Mock(return_value=[])
    guide_handler = Mock(
        return_value=[
            {
                "citation_id": "umsetzungsleitfaden:92",
                "excerpt": "Rechtsgrundlage für den Versand",
            }
        ]
    )
    fake_client = FakeOpenAI(
        [
            _tool_completion(
                _function_call(
                    "search_schema",
                    '{"query":"Meldebegründung"}',
                    call_id="call_1",
                )
            ),
            _final_completion(
                premature_answer,
                supported=False,
            ),
            _tool_completion(
                _function_call(
                    "search_umsetzungsleitfaden",
                    '{"query":"Meldebegründung"}',
                    call_id="call_2",
                )
            ),
            _final_completion(
                "Die Meldebegründung nennt die Rechtsgrundlage.",
                ("umsetzungsleitfaden:92",),
            ),
        ]
    )
    schema_tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_schema(),
        handler=schema_handler,
        follow_up_tools=("get_schema_element",),
    )
    exact_tool = LocalTool(
        name="get_schema_element",
        description="Get all paths for an exact element name.",
        parameters=_schema(),
        handler=exact_handler,
    )
    guide_tool = LocalTool(
        name="search_umsetzungsleitfaden",
        description="Search the official implementation guide.",
        parameters=_schema(),
        handler=guide_handler,
    )

    result = answer_question(
        "Was beschreibt die Meldebegründung fachlich?",
        tools=[schema_tool, exact_tool, guide_tool],
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
    )

    assert result.answer == "Die Meldebegründung nennt die Rechtsgrundlage."
    assert result.citation_ids == ("umsetzungsleitfaden:92",)
    assert [execution.name for execution in result.tool_executions] == [
        "search_schema",
        "search_umsetzungsleitfaden",
    ]
    assert schema_handler.call_args_list == [call(query="Meldebegründung")]
    exact_handler.assert_not_called()
    assert guide_handler.call_args_list == [call(query="Meldebegründung")]
    assert [
        request["tool_choice"] for request in fake_client.chat.completions.calls
    ] == ["required", "auto", "required", "auto"]
    recovery_tools = fake_client.chat.completions.calls[2]["tools"]
    assert [tool["function"]["name"] for tool in recovery_tools] == [
        "get_schema_element",
        "search_umsetzungsleitfaden",
    ]


def test_answer_question_does_not_execute_duplicate_recovery_call() -> None:
    schema_handler = Mock(return_value=[])
    guide_handler = Mock(return_value=[])
    duplicate_call = _function_call(
        "search_schema",
        '{"version":null,"query":"Meldebegründung"}',
        call_id="call_2",
    )
    fake_client = FakeOpenAI(
        [
            _tool_completion(
                _function_call(
                    "search_schema",
                    '{"query":"Meldebegründung","version":null}',
                    call_id="call_1",
                )
            ),
            _final_completion("Keine ausreichende Evidenz.", supported=False),
            _tool_completion(duplicate_call),
            _final_completion("Keine ausreichende Evidenz.", supported=False),
        ]
    )
    schema_tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_versioned_schema(),
        handler=schema_handler,
    )
    guide_tool = LocalTool(
        name="search_umsetzungsleitfaden",
        description="Search the official implementation guide.",
        parameters=_schema(),
        handler=guide_handler,
    )

    result = answer_question(
        "Was beschreibt die Meldebegründung fachlich?",
        tools=[schema_tool, guide_tool],
        client=cast(OpenAI, fake_client),
        endpoint=_endpoint(),
        max_recovery_attempts=1,
    )

    schema_handler.assert_called_once_with(query="Meldebegründung", version=None)
    guide_handler.assert_not_called()
    assert len(result.tool_executions) == 2
    duplicate_execution = result.tool_executions[1]
    assert duplicate_execution.error == "duplicate_tool_call"
    assert duplicate_execution.result == {
        "error": "duplicate_tool_call",
        "message": (
            "Dieser Tool-Aufruf wurde bereits ausgeführt. Verwenden Sie das "
            "vorherige Ergebnis oder rufe ein anderes Tool auf bzw. gebe andere "
            "Argumente an."
        ),
        "tool": "search_schema",
    }
    assert fake_client.chat.completions.calls[3]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_2",
        "content": duplicate_execution.output,
    }


def test_answer_question_rejects_negative_recovery_limit() -> None:
    with pytest.raises(ValueError, match="max_recovery_attempts"):
        answer_question("Test", max_recovery_attempts=-1)


def test_answer_question_enforces_tool_round_limit() -> None:
    fake_client = FakeOpenAI(
        [
            _tool_completion(
                _function_call(
                    "search_schema",
                    '{"query":"first"}',
                    call_id="call_1",
                )
            ),
            _tool_completion(
                _function_call(
                    "search_schema",
                    '{"query":"second"}',
                    call_id="call_2",
                )
            ),
        ]
    )
    handler = Mock(return_value=[])
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_schema(),
        handler=handler,
    )

    with pytest.raises(ToolCallError, match="tool-call limit exceeded"):
        answer_question(
            "Test",
            tools=[tool],
            client=cast(OpenAI, fake_client),
            endpoint=_endpoint(),
            max_tool_rounds=1,
        )

    handler.assert_called_once_with(query="first")


def test_answer_question_enforces_version_constraint_on_tool_calls() -> None:
    function_call = _function_call(
        "search_schema",
        '{"query":"Diagnose","version":"3.0.0"}',
    )
    fake_client = FakeOpenAI(
        [
            _tool_completion(function_call),
            _final_completion("Antwort für 3.0.5."),
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
    instructions = fake_client.chat.completions.calls[0]["messages"][0]["content"]
    assert "Verwende ausschließlich die oBDS-Version 3.0.5" in instructions


def test_answer_question_applies_default_version_to_null_tool_argument() -> None:
    function_call = _function_call(
        "search_schema",
        '{"query":"Diagnose","version":null}',
    )
    fake_client = FakeOpenAI(
        [
            _tool_completion(function_call),
            _final_completion("Antwort für 3.0.5."),
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
            _tool_completion(*calls),
            _final_completion("Vergleich."),
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
            _tool_completion(function_call),
            _final_completion("Version nicht verfügbar."),
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
    assert fake_client.chat.completions.calls[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": execution.output,
    }


def test_answer_question_rejects_unknown_tool_call() -> None:
    function_call = _function_call("unknown", "{}")
    fake_client = FakeOpenAI([_tool_completion(function_call)])

    with pytest.raises(ToolCallError, match="Unknown tool"):
        answer_question(
            "Test",
            client=cast(OpenAI, fake_client),
            endpoint=_endpoint(),
        )


def test_answer_question_rejects_supported_answer_without_citations() -> None:
    fake_client = FakeOpenAI([_final_completion("Unbelegte Antwort.", supported=True)])

    with pytest.raises(ToolCallError, match="without citations"):
        answer_question(
            "Test",
            client=cast(OpenAI, fake_client),
            endpoint=_endpoint(),
        )

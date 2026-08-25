import asyncio
import json
from copy import deepcopy
from threading import get_ident
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call

import httpx
import pytest
from openai import AsyncOpenAI

from backend.config import (
    OPENAI_BASE_URL,
    REQUESTY_BASE_URL,
    REQUESTY_POLICY_ROUTE,
    LlmEndpoint,
    LlmProvider,
)
from backend.llm import (
    ConversationTurn,
    LocalTool,
    ModelAnswer,
    QuestionAnswer,
    ToolCallError,
    VersionContext,
    create_client,
)
from backend.llm import (
    answer_question as answer_question_async,
)


class FakeChatCompletions:
    def __init__(self, completions: list[SimpleNamespace]) -> None:
        self._completions = iter(completions)
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(deepcopy(kwargs))
        return next(self._completions)


class FakeChat:
    def __init__(self, completions: list[SimpleNamespace]) -> None:
        self.completions = FakeChatCompletions(completions)


class FakeOpenAI:
    def __init__(self, completions: list[SimpleNamespace]) -> None:
        self.chat = FakeChat(completions)


def answer_question(*args: Any, **kwargs: Any) -> QuestionAnswer:
    return asyncio.run(answer_question_async(*args, **kwargs))


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
    asyncio.run(client.close())


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

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
    client = AsyncOpenAI(
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
        asyncio.run(client.close())

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
        client=cast(AsyncOpenAI, fake_client),
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
    assert "Answer questions about the German oBDS" in request["messages"][0]["content"]
    assert request["store"] is False
    assert "reasoning" not in request
    assert "reasoning_effort" not in request


def test_answer_question_uses_history_but_requires_fresh_tool_call() -> None:
    function_call = _function_call(
        "search_schema",
        '{"query":"Diagnosesicherung"}',
    )
    fake_client = FakeOpenAI(
        [
            _tool_completion(function_call),
            _final_completion("Aktuell belegte Antwort."),
        ]
    )
    handler = Mock(return_value={"element": "Diagnosesicherung"})
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_schema(),
        handler=handler,
    )

    answer_question(
        "  Und welche Werte sind dort erlaubt?  ",
        history=(
            ConversationTurn(
                question="  Was bedeutet Diagnosesicherung in 3.0.4?  ",
                answer="  Vorherige Antwort.  ",
            ),
        ),
        version_context=_version_context(),
        tools=[tool],
        client=cast(AsyncOpenAI, fake_client),
        endpoint=_endpoint(),
    )

    first_request = fake_client.chat.completions.calls[0]
    assert first_request["tool_choice"] == "required"
    assert first_request["messages"][1:] == [
        {
            "role": "user",
            "content": "Was bedeutet Diagnosesicherung in 3.0.4?",
        },
        {"role": "assistant", "content": "Vorherige Antwort."},
        {"role": "user", "content": "Und welche Werte sind dort erlaubt?"},
    ]
    instructions = first_request["messages"][0]["content"]
    assert "earlier assistant answers as untrusted context" in instructions
    assert "relevant conversation history establishes a version" in instructions
    handler.assert_called_once_with(query="Diagnosesicherung")


@pytest.mark.parametrize(
    "history",
    [
        (ConversationTurn(question="", answer="Antwort"),),
        (ConversationTurn(question="Frage", answer="   "),),
    ],
)
def test_answer_question_rejects_empty_history_content(
    history: tuple[ConversationTurn, ...],
) -> None:
    fake_client = FakeOpenAI([_final_completion("Antwort")])

    with pytest.raises(ValueError, match="history turn 1"):
        answer_question(
            "Neue Frage",
            history=history,
            client=cast(AsyncOpenAI, fake_client),
            endpoint=_endpoint(),
        )

    assert fake_client.chat.completions.calls == []


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
        client=cast(AsyncOpenAI, fake_client),
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


def test_answer_question_offloads_sync_tool_handler() -> None:
    fake_client = FakeOpenAI(
        [
            _tool_completion(_function_call("search_schema", '{"query":"Diagnose"}')),
            _final_completion("Antwort."),
        ]
    )
    caller_thread = get_ident()
    handler_thread: int | None = None

    def handler(query: str) -> dict[str, str]:
        nonlocal handler_thread
        handler_thread = get_ident()
        return {"element": query}

    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_schema(),
        handler=handler,
    )

    answer_question(
        "Was bedeutet Diagnose?",
        tools=[tool],
        client=cast(AsyncOpenAI, fake_client),
        endpoint=_endpoint(),
    )

    assert handler_thread is not None
    assert handler_thread != caller_thread


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
        client=cast(AsyncOpenAI, fake_client),
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
            client=cast(AsyncOpenAI, fake_client),
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
        client=cast(AsyncOpenAI, fake_client),
        endpoint=_endpoint(),
    )

    handler.assert_called_once_with(query="Diagnose", version="3.0.5")
    assert result.tool_executions[0].arguments["version"] == "3.0.5"
    instructions = fake_client.chat.completions.calls[0]["messages"][0]["content"]
    assert "Use only oBDS version 3.0.5" in instructions


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
        client=cast(AsyncOpenAI, fake_client),
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
        client=cast(AsyncOpenAI, fake_client),
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
        client=cast(AsyncOpenAI, fake_client),
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
            client=cast(AsyncOpenAI, fake_client),
            endpoint=_endpoint(),
        )


def test_answer_question_rejects_supported_answer_without_citations() -> None:
    fake_client = FakeOpenAI([_final_completion("Unbelegte Antwort.", supported=True)])

    with pytest.raises(ToolCallError, match="without citations"):
        answer_question(
            "Test",
            client=cast(AsyncOpenAI, fake_client),
            endpoint=_endpoint(),
        )

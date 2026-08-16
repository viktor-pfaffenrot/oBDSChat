from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call

import pytest
from openai import OpenAI

from backend.config import Settings
from backend.llm import (
    LocalTool,
    ModelAnswer,
    ToolCallError,
    VersionContext,
    answer_question,
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


def test_answer_question_uses_configured_model() -> None:
    fake_client = FakeOpenAI([_final_response("Diagnosesicherung erklärt.")])

    result = answer_question(
        "Was bedeutet Diagnosesicherung?",
        client=cast(OpenAI, fake_client),
        settings=Settings(openai_model="gpt-5.6-terra"),
    )

    assert result.answer == "Diagnosesicherung erklärt."
    assert result.tool_executions == ()
    assert fake_client.responses.calls[0]["model"] == "gpt-5.6-terra"
    assert fake_client.responses.calls[0]["store"] is False
    assert fake_client.responses.calls[0]["text_format"] is ModelAnswer


def test_answer_question_executes_function_call() -> None:
    reasoning_item = SimpleNamespace(type="reasoning")
    function_call = SimpleNamespace(
        type="function_call",
        name="search_schema",
        arguments='{"query":"Diagnosesicherung"}',
        call_id="call_1",
    )
    fake_client = FakeOpenAI(
        [
            SimpleNamespace(output=[reasoning_item, function_call], output_text=""),
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
        settings=Settings(),
    )

    assert result.answer == "Belegte Antwort."
    assert result.citation_ids == ("source:1",)
    assert len(result.tool_executions) == 1
    assert result.tool_executions[0].name == "search_schema"
    assert result.tool_executions[0].result == {"element": "Diagnosesicherung"}
    definition = fake_client.responses.calls[0]["tools"][0]
    assert definition["strict"] is True
    assert fake_client.responses.calls[0]["tool_choice"] == "required"
    assert fake_client.responses.calls[1]["tool_choice"] == "auto"
    next_input = fake_client.responses.calls[1]["input"]
    assert reasoning_item in next_input
    assert {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"element": "Diagnosesicherung"}',
    } in next_input


def test_answer_question_enforces_version_constraint_on_tool_calls() -> None:
    function_call = SimpleNamespace(
        type="function_call",
        name="search_schema",
        arguments='{"query":"Diagnose","version":"3.0.0"}',
        call_id="call_1",
    )
    fake_client = FakeOpenAI(
        [
            SimpleNamespace(output=[function_call], output_text=""),
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
        settings=Settings(),
    )

    handler.assert_called_once_with(query="Diagnose", version="3.0.5")
    assert result.tool_executions[0].arguments["version"] == "3.0.5"
    assert (
        "Use only oBDS version 3.0.5" in fake_client.responses.calls[0]["instructions"]
    )


def test_answer_question_applies_default_version_to_null_tool_argument() -> None:
    function_call = SimpleNamespace(
        type="function_call",
        name="search_schema",
        arguments='{"query":"Diagnose","version":null}',
        call_id="call_1",
    )
    fake_client = FakeOpenAI(
        [
            SimpleNamespace(output=[function_call], output_text=""),
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
        settings=Settings(),
    )

    handler.assert_called_once_with(query="Diagnose", version="3.0.5")
    assert result.tool_executions[0].arguments["version"] == "3.0.5"


def test_answer_question_preserves_versions_for_comparison() -> None:
    calls = [
        SimpleNamespace(
            type="function_call",
            name="search_schema",
            arguments=f'{{"query":"Änderungen","version":"{version}"}}',
            call_id=f"call_{version}",
        )
        for version in ("3.0.4", "3.0.5")
    ]
    fake_client = FakeOpenAI(
        [
            SimpleNamespace(output=calls, output_text=""),
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
        settings=Settings(),
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
    function_call = SimpleNamespace(
        type="function_call",
        name="search_schema",
        arguments='{"query":"Diagnose","version":"9.9.9"}',
        call_id="call_1",
    )
    fake_client = FakeOpenAI(
        [
            SimpleNamespace(output=[function_call], output_text=""),
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
        settings=Settings(),
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
    function_call = SimpleNamespace(
        type="function_call",
        name="unknown",
        arguments="{}",
        call_id="call_1",
    )
    fake_client = FakeOpenAI([SimpleNamespace(output=[function_call], output_text="")])

    with pytest.raises(ToolCallError, match="Unknown tool"):
        answer_question(
            "Test",
            client=cast(OpenAI, fake_client),
            settings=Settings(),
        )


def test_answer_question_rejects_supported_answer_without_citations() -> None:
    fake_client = FakeOpenAI([_final_response("Unbelegte Antwort.", supported=True)])

    with pytest.raises(ToolCallError, match="without citations"):
        answer_question(
            "Test",
            client=cast(OpenAI, fake_client),
            settings=Settings(),
        )

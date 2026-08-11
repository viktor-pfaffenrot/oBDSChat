from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import OpenAI

from backend.config import Settings
from backend.llm import LocalTool, ToolCallError, answer_question


class FakeResponses:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
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


def test_answer_question_uses_configured_model() -> None:
    fake_client = FakeOpenAI(
        [SimpleNamespace(output=[], output_text="Diagnosesicherung erklärt.")]
    )

    answer = answer_question(
        "Was bedeutet Diagnosesicherung?",
        client=cast(OpenAI, fake_client),
        settings=Settings(openai_model="gpt-5.6-terra"),
    )

    assert answer == "Diagnosesicherung erklärt."
    assert fake_client.responses.calls[0]["model"] == "gpt-5.6-terra"
    assert fake_client.responses.calls[0]["store"] is False


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
            SimpleNamespace(output=[], output_text="Belegte Antwort."),
        ]
    )
    tool = LocalTool(
        name="search_schema",
        description="Search the local oBDS schema.",
        parameters=_schema(),
        handler=lambda query: {"element": query},
    )

    answer = answer_question(
        "Welche Werte sind erlaubt?",
        tools=[tool],
        client=cast(OpenAI, fake_client),
        settings=Settings(),
    )

    assert answer == "Belegte Antwort."
    definition = fake_client.responses.calls[0]["tools"][0]
    assert definition["strict"] is True
    next_input = fake_client.responses.calls[1]["input"]
    assert reasoning_item in next_input
    assert {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"element": "Diagnosesicherung"}',
    } in next_input


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

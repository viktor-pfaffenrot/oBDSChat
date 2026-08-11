"""OpenAI Responses API integration for the backend."""

import json
from collections.abc import Callable, Sequence
from typing import Any, Self

from openai import OpenAI
from openai.types.responses import FunctionToolParam, ResponseFunctionToolCall
from pydantic import BaseModel, ConfigDict, model_validator

from backend.config import Settings, load_settings

ToolHandler = Callable[..., object]


class ToolCallError(RuntimeError):
    """Raised when a model-requested local tool cannot be executed."""


class LocalTool(BaseModel):
    """A strict OpenAI function definition paired with its local handler."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler

    @model_validator(mode="after")
    def validate_strict_schema(self) -> Self:
        """Validate requirements imposed by OpenAI strict function tools."""
        properties = self.parameters.get("properties")
        required = self.parameters.get("required")
        if self.parameters.get("type") != "object":
            raise ValueError("Tool parameters must use an object schema")
        if not isinstance(properties, dict):
            raise TypeError("Tool parameters must define properties")
        if not isinstance(required, list):
            raise TypeError("Tool parameters must define required fields")
        if set(required) != set(properties):
            raise ValueError("Strict tools must require every property")
        if self.parameters.get("additionalProperties") is not False:
            raise ValueError("Strict tools must reject additional properties")
        return self

    def as_openai_tool(self) -> FunctionToolParam:
        """Return the Responses API function definition."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": True,
        }


def create_client(settings: Settings | None = None) -> OpenAI:
    """Create the backend OpenAI client from validated settings."""
    resolved_settings = settings or load_settings()
    return OpenAI(api_key=resolved_settings.require_openai_api_key())


def answer_question(
    question: str,
    *,
    tools: Sequence[LocalTool] = (),
    client: OpenAI | None = None,
    settings: Settings | None = None,
    max_tool_rounds: int = 4,
) -> str:
    """Answer a question, executing local function calls requested by the model."""
    if not question.strip():
        raise ValueError("question must not be empty")
    if max_tool_rounds < 1:
        raise ValueError("max_tool_rounds must be at least 1")

    resolved_settings = settings or load_settings()
    resolved_client = client or create_client(resolved_settings)
    tools_by_name = _index_tools(tools)
    tool_definitions = [tool.as_openai_tool() for tool in tools]
    input_items: list[Any] = [{"role": "user", "content": question}]

    for tool_round in range(max_tool_rounds + 1):
        response = resolved_client.responses.create(
            model=resolved_settings.openai_model,
            input=input_items,
            tools=tool_definitions,
            store=False,
        )
        input_items.extend(response.output)
        function_calls = [
            item for item in response.output if item.type == "function_call"
        ]
        if not function_calls:
            return response.output_text
        if tool_round == max_tool_rounds:
            raise ToolCallError("OpenAI tool-call limit exceeded")

        for function_call in function_calls:
            output = _execute_tool_call(function_call, tools_by_name)
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": function_call.call_id,
                    "output": output,
                }
            )

    raise AssertionError("unreachable")


def _index_tools(tools: Sequence[LocalTool]) -> dict[str, LocalTool]:
    tools_by_name = {tool.name: tool for tool in tools}
    if len(tools_by_name) != len(tools):
        raise ValueError("Tool names must be unique")
    return tools_by_name


def _execute_tool_call(
    function_call: ResponseFunctionToolCall,
    tools: dict[str, LocalTool],
) -> str:
    tool = tools.get(function_call.name)
    if tool is None:
        raise ToolCallError(f"Unknown tool requested: {function_call.name}")

    try:
        arguments = json.loads(function_call.arguments)
    except json.JSONDecodeError as error:
        raise ToolCallError(
            f"Invalid arguments for tool {function_call.name}"
        ) from error
    if not isinstance(arguments, dict):
        raise ToolCallError(
            f"Arguments for tool {function_call.name} must be an object"
        )

    result = tool.handler(**arguments)
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise ToolCallError(
            f"Tool {function_call.name} returned a non-JSON value"
        ) from error

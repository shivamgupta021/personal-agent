"""Fixed delegation for tests focused on a specialist, not model routing quality."""
from collections.abc import Callable

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo


def delegated(respond: Callable[[list[ModelMessage], AgentInfo], ModelResponse], specialist: str = 'grievance') -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if info.output_tools and set(info.output_tools[0].parameters_json_schema.get('properties', {})) == {'specialist'}:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist': specialist})])
        return respond(messages, info)
    return reply

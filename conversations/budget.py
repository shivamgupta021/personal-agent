"""Reject oversized requests before each model call, including tool results."""

from dataclasses import asdict
import json
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext

from conversations import constants


class ContextBudgetExceeded(Exception):
    pass


class ConversationBudget(AbstractCapability[None]):
    @staticmethod
    def size(request_context: ModelRequestContext) -> int:
        # Normal agent requests carry active instructions in instruction_parts below.
        # Historical per-message instructions and local provenance are not model input.
        excluded: dict[str, Any] = {'metadata': True, 'parts': {'__all__': {'metadata'}}}
        if request_context.model_request_parameters.instruction_parts is not None:
            excluded['instructions'] = True
        messages = ModelMessagesTypeAdapter.dump_json(
            request_context.messages, exclude={'__all__': excluded},
        )
        parameters = json.dumps(asdict(request_context.model_request_parameters), default=str).encode()
        return len(messages) + len(parameters) + constants.REQUEST_HEADROOM

    async def before_model_request(self, ctx: RunContext[None], request_context: ModelRequestContext) -> ModelRequestContext:
        if self.size(request_context) > constants.MAX_REQUEST_BYTES:
            raise ContextBudgetExceeded
        return request_context

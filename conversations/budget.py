"""Reject oversized requests before each model call, including tool results."""

from dataclasses import asdict
import json

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext

from conversations import constants


class ContextBudgetExceeded(Exception):
    pass


class ConversationBudget(AbstractCapability[None]):
    async def before_model_request(self, ctx: RunContext[None], request_context: ModelRequestContext) -> ModelRequestContext:
        messages = ModelMessagesTypeAdapter.dump_json(request_context.messages)
        parameters = json.dumps(asdict(request_context.model_request_parameters), default=str).encode()
        if len(messages) + len(parameters) + constants.REQUEST_HEADROOM > constants.MAX_REQUEST_BYTES:
            raise ContextBudgetExceeded
        return request_context

"""Classify which specialist owns a request, without interpreting its task."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from core.routing_constants import ROUTING_OUTPUT_TOKENS, ROUTING_TIMEOUT_SECONDS, ROUTING_RETRIES, ROUTING_REQUEST_LIMIT, ROUTING_HISTORY_MESSAGES, ROUTING_HISTORY_CHARS
from core.routing_prompts import ROUTING_INSTRUCTIONS
from core.tracing import tracing
from core.turns import OwnerTurn
from grievances.store import CaseStore
from conversations.store import ConversationStore
from conversations.direct import DirectConversation


class Route(BaseModel):
    model_config = ConfigDict(extra='forbid')
    specialist: Literal['grievance', 'preferences', 'conversation', 'research', 'attachments']


class IntentRouter:
    def __init__(self, model: Model) -> None:
        self.agent = Agent(model, output_type=Route, instructions=ROUTING_INSTRUCTIONS,
            name='orchestrator', retries=ROUTING_RETRIES,
            model_settings={'max_tokens': ROUTING_OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()

    async def route(self, turn: OwnerTurn) -> Route:
        selection = await CaseStore().selection(turn.owner_id, turn.conversation_id)
        context = await ConversationStore().context(turn.owner_id)
        recent = DirectConversation.dialogue(context)[-ROUTING_HISTORY_MESSAGES:]
        remaining = ROUTING_HISTORY_CHARS
        history: list[dict[str, str]] = []
        for message in reversed(recent):
            content = message['content'][-remaining:]
            history.insert(0, {'kind': message['kind'], 'content': content})
            remaining -= len(content)
            if remaining <= 0:
                break
        with tracing.operation('orchestrator.route', {'message': turn.text}, kind='agent', private=True) as span:
            result = await self.agent.run(json.dumps({'message': turn.text,
                'has_selected_case': bool(selection.case_id), 'recent_conversation': history}),
                usage_limits=UsageLimits(request_limit=ROUTING_REQUEST_LIMIT), timeout=ROUTING_TIMEOUT_SECONDS)
            span.set_attribute('orchestrator.specialist', result.output.specialist)
            tracing.record_output(span, result.output.model_dump())
            return result.output

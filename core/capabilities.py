"""Deterministic introduction to the capabilities this application actually exposes."""

import asyncio

from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart

from conversations.store import ConversationStore, ContextChanged
from core.capability_constants import CAPABILITY_REQUESTS, CAPABILITY_DESCRIPTION
from core.tracing import tracing
from core.turns import OwnerTurn


class CapabilityHelp:
    @staticmethod
    def matches(message: str) -> bool:
        normalized = ' '.join(message.strip().rstrip('?.!').casefold().replace('!', ',').split())
        return normalized in CAPABILITY_REQUESTS

    async def run(self, turn: OwnerTurn) -> str:
        with tracing.operation('application.help', {'message': turn.text}, private=True) as span:
            store = ConversationStore()
            context = await store.context(turn.owner_id)
            messages = [*context.messages, ModelRequest(parts=[UserPromptPart(turn.text)]),
                ModelResponse(parts=[TextPart(CAPABILITY_DESCRIPTION)])]
            context.capture(messages)
            commit = asyncio.create_task(store.save(turn.owner_id, messages, context))
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                await commit
                raise
            except ContextChanged:
                return 'Your conversation changed while I was responding. Please ask again.'
            tracing.record_output(span, {'answer': CAPABILITY_DESCRIPTION})
            return CAPABILITY_DESCRIPTION

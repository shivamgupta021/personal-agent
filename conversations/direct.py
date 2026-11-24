"""Persist a plain conversational answer already produced during routing."""
import asyncio

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart, CompactionPart

from conversations.context import ConversationContext
from conversations.store import ConversationStore, ContextChanged
from core.tracing import tracing
from core.turns import OwnerTurn


class DirectConversation:
    @staticmethod
    def dialogue(context: ConversationContext) -> list[dict[str, str]]:
        return [{'kind': part.part_kind, 'content': part.content}
                for message in context.messages for part in message.parts
                if isinstance(part, (UserPromptPart, TextPart, CompactionPart)) and isinstance(part.content, str)]

    async def save(self, turn: OwnerTurn, answer: str, context: ConversationContext) -> str:
        with tracing.operation('conversation.direct', {'message': turn.text}, private=True) as span:
            messages = [*context.messages, ModelRequest(parts=[UserPromptPart(turn.text)]),
                        ModelResponse(parts=[TextPart(answer)])]
            context.capture(messages)
            commit = asyncio.create_task(ConversationStore().save(turn.owner_id, messages, context))
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                await commit
                raise
            except ContextChanged:
                return 'Your conversation changed while I was responding. Please ask again.'
            tracing.record_output(span, {'answer': answer})
            return answer

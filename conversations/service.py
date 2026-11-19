"""A durable turn around the existing research loop."""

from collections.abc import Awaitable, Callable
from typing import Protocol

from pydantic_ai.run import AgentRunResult

from conversations.budget import ContextBudgetExceeded
from conversations.context import ConversationContext
from conversations.recall import ArchiveRecall
from conversations.cleanup import TraceCleanup
from conversations.constants import DELETE_CONVERSATION_PHRASES
from conversations.store import ConversationStore
from core.tracing import tracing


class ConversationRunner(Protocol):
    async def run_turn(self, message: str, context: ConversationContext,
                       forget: Callable[[], Awaitable[str]], recall: ArchiveRecall) -> AgentRunResult[str]: ...


class ConversationDeleted(Exception):
    """End the run immediately so deleted context cannot generate a new answer."""


class ConversationService:
    def __init__(self, identity: str, runner: ConversationRunner,
                 cleanup: Callable[[str], Awaitable[str]] = TraceCleanup.configured, *, compact: bool = True) -> None:
        self.identity = identity
        self.runner = runner
        self.cleanup = cleanup
        self.compact = compact
        self.store = ConversationStore()

    async def run(self, message: str) -> str:
        control = message.strip().rstrip('.!').casefold()
        if control in DELETE_CONVERSATION_PHRASES:
            await self.store.forget(self.identity)
            return 'Conversation deleted locally. ' + await self.cleanup(self.identity)
        if control == 'retry trace cleanup':
            return await self.cleanup(self.identity)
        context = ConversationContext(await self.store.history(self.identity), compact=self.compact)
        try:
            with tracing.operation('conversation.turn', {'message': message}, kind='agent') as span:
                span.set_attribute('langfuse.session.id', self.identity)
                trace_id = format(span.get_span_context().trace_id, '032x')
                if span.is_recording():
                    await self.store.track(self.identity, trace_id)
                result = await self.runner.run_turn(message, context, self.forget_conversation, ArchiveRecall(self.identity, self.store))
                context.capture(result.all_messages())
                await self.store.save(self.identity, result.all_messages(), context)
                tracing.record_output(span, {'answer': result.output})
                return result.output
        except ContextBudgetExceeded:
            return 'This conversation exceeds the current context budget. Your saved history is unchanged. Compaction could not safely fit this request. Try a smaller message.'
        except ConversationDeleted:
            return 'Conversation deleted locally. ' + await self.cleanup(self.identity)

    async def forget_conversation(self) -> str:
        """Delete all stored conversation history, only on the owner's explicit request."""
        await self.store.forget(self.identity)
        raise ConversationDeleted

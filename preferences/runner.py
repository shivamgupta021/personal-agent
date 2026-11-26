"""Run an already-routed preference request without another tool-selection model."""

from collections.abc import Awaitable, Callable

from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResult
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

from core.tracing import tracing
from conversations.context import ConversationContext
from conversations.facts import FactCleanup
from conversations.recall import ArchiveRecall
from preferences.service import PreferenceReply, PreferenceService
from preferences.schema import PreferenceIntent


class PreferenceRunner:
    def __init__(self, model: Model, usage: RunUsage, intent: PreferenceIntent | None = None) -> None:
        self.model = model
        self.usage = usage
        self.intent = intent

    async def run_turn(self, message: str, context: ConversationContext,
                       forget: Callable[[], Awaitable[str]], recall: ArchiveRecall,
                       facts: FactCleanup) -> AgentRunResult[str]:
        preferences = PreferenceService(self.model, facts, private=True)
        await preferences.instructions()
        ctx = RunContext(deps=None, model=self.model, usage=self.usage)
        with tracing.operation('preferences.manage', {'request': message}, kind='tool', private=True) as span:
            try:
                result = (await preferences.execute_intent(ctx, self.intent) if self.intent is not None
                          else await preferences.manage_preferences(ctx))
                receipt = result.message
                await preferences.persist_reply(receipt)
            except PreferenceReply as reply:
                receipt = str(reply)
            tracing.record_output(span, {'answer': receipt})
        raise PreferenceReply(receipt)

"""Run an already-routed preference request without another tool-selection model."""

from collections.abc import Awaitable, Callable

from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResult
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

from conversations.context import ConversationContext
from conversations.facts import FactCleanup
from conversations.recall import ArchiveRecall
from preferences.service import PreferenceReply, PreferenceService


class PreferenceRunner:
    def __init__(self, model: Model, usage: RunUsage) -> None:
        self.model = model
        self.usage = usage

    async def run_turn(self, message: str, context: ConversationContext,
                       forget: Callable[[], Awaitable[str]], recall: ArchiveRecall,
                       facts: FactCleanup) -> AgentRunResult[str]:
        preferences = PreferenceService(self.model, facts, private=True)
        await preferences.instructions()
        result = await preferences.manage_preferences(RunContext(deps=None, model=self.model, usage=self.usage))
        # Mutations already end with an application-owned receipt. A clarification
        # also needs no language-model paraphrase, but must survive a restart.
        await preferences.persist_reply(result.message)
        raise PreferenceReply(result.message)

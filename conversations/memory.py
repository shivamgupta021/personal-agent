"""Persistent conversation and explicit memory operations without web or case authority."""

from collections.abc import Awaitable, Callable

from pydantic_ai import Agent, Tool
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResult
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import UsageLimits

from conversations.compaction import ConversationCompaction
from conversations.constants import MAX_OUTPUT_TOKENS
from conversations.context import ConversationContext
from conversations.facts import FactCleanup, FactEditor
from conversations.recall import ArchiveRecall
from conversations.prompts import CONVERSATION_INSTRUCTIONS, MEMORY_INSTRUCTIONS
from core.tracing import tracing
from preferences.service import PreferenceService
from research import constants


class ConversationMemoryRunner:
    def __init__(self, model: Model, *, read_only: bool = False) -> None:
        self.model = model
        self.read_only = read_only
        self.agent: Agent[None, str] = Agent(model, instructions=MEMORY_INSTRUCTIONS,
            name='conversation_memory', retries=constants.AGENT_VALIDATION_RETRIES,
            model_settings={'max_tokens': MAX_OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()

    async def run_turn(self, message: str, context: ConversationContext,
                       forget: Callable[[], Awaitable[str]], recall: ArchiveRecall, facts: FactCleanup) -> AgentRunResult[str]:
        preferences = PreferenceService(self.model, facts, private=True)
        editor = FactEditor(self.model, facts, private=True)
        tools = [Tool(recall.recall_conversation)]
        if not self.read_only:
            tools.extend([Tool(preferences.manage_preferences), Tool(forget, name='forget_conversation'),
                          Tool(editor.edit_conversation_fact)])
        return await self.agent.run(message, message_history=context.messages,
            instructions=CONVERSATION_INSTRUCTIONS + await preferences.instructions(),
            capabilities=[ConversationCompaction(context, private=True)],
            toolsets=[FunctionToolset(tools=tools)],
            usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS, tool_calls_limit=constants.MAX_TOOL_CALLS),
            timeout=constants.RUN_TIMEOUT_SECONDS)

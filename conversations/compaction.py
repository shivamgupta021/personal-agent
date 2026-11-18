"""Budget-aware adapter around the framework's summarization capability."""

import asyncio
import logging

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext
from pydantic_ai_harness.compaction import SummarizingCompaction

from conversations import constants
from conversations.budget import ConversationBudget
from conversations.context import ConversationContext
from conversations.prompts import SUMMARY_PROMPT

logger = logging.getLogger(__name__)


class ConversationCompaction(AbstractCapability[None]):
    def __init__(self, context: ConversationContext) -> None:
        self.context = context
        self.attempts = 0
        self.summarizer: SummarizingCompaction[None] = SummarizingCompaction(
            max_tokens=1, keep_tokens=constants.RECENT_CONTEXT_BYTES,
            tokenizer=self.byte_count, preserve_first_user_message=False,
            tool_return_max_chars=None, summary_prompt=SUMMARY_PROMPT,
            model_settings={'max_tokens': constants.SUMMARY_OUTPUT_TOKENS},
            summarization_capabilities=[ConversationBudget()], retries=constants.SUMMARY_RETRIES,
        )

    @staticmethod
    def byte_count(text: str) -> int:
        return len(text.encode())

    async def before_model_request(self, ctx: RunContext[None], request_context: ModelRequestContext) -> ModelRequestContext:
        self.context.capture(request_context.messages)
        before = list(request_context.messages)
        size = ConversationBudget.size(request_context)
        if self.context.compact and size > constants.COMPACTION_TRIGGER_BYTES and self.attempts < constants.MAX_COMPACTIONS_PER_TURN:
            self.attempts += 1
            try:
                async with asyncio.timeout(constants.SUMMARY_TIMEOUT_SECONDS):
                    compacted = await self.summarizer.before_model_request(ctx, request_context)
                if ConversationBudget.size(compacted) < size:
                    self.context.record_summary(before, compacted.messages)
                    request_context = compacted
                else:
                    ctx.messages[:] = before
            except Exception:
                # Cancellation propagates. A failed summary never replaces committed context.
                ctx.messages[:] = before
                logger.warning('Conversation summarization unavailable; retaining prior context')
        return await ConversationBudget().before_model_request(ctx, request_context)

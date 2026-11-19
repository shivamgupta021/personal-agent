"""Main research agent routing to query planners and evidence-reading tools."""

from collections.abc import Awaitable, Callable

from pydantic_ai import Agent, Tool
from pydantic_ai.run import AgentRunResult
from pydantic_ai.toolsets import FunctionToolset

from conversations.compaction import ConversationCompaction
from conversations.context import ConversationContext
from conversations.recall import ArchiveRecall
from conversations.facts import FactCleanup, FactEditor
from conversations.constants import MAX_OUTPUT_TOKENS
from conversations.prompts import CONVERSATION_INSTRUCTIONS
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from research import constants
from research.tools.firecrawl import Firecrawl
from research.tools.search import ResearchSearch
from research.planning.agent import PlanningMode, QueryPlanner
from research.prompts import DIRECT_RESEARCH_INSTRUCTIONS, DELEGATED_RESEARCH_INSTRUCTIONS


class ResearchAgent:
    def __init__(
        self, model: Model, firecrawl: Firecrawl,
        simple_model: Model | None = None, complex_model: Model | None = None,
        *, use_query_planners: bool = False,
    ) -> None:
        self.model = model
        tools = [Tool(firecrawl.search), Tool(firecrawl.read_page)]
        instructions = DIRECT_RESEARCH_INSTRUCTIONS
        if use_query_planners:
            search = ResearchSearch(
                firecrawl, QueryPlanner(simple_model or model, PlanningMode.SIMPLE),
                QueryPlanner(complex_model or model, PlanningMode.COMPLEX),
            )
            tools = [Tool(search.search_simple), Tool(search.search_complex), Tool(firecrawl.read_page)]
            instructions = DELEGATED_RESEARCH_INSTRUCTIONS
        self.agent: Agent[None, str] = Agent(
            model,
            instructions=instructions,
            name='research',
            retries=constants.AGENT_VALIDATION_RETRIES,
            tools=tools,
        )

    async def run(self, message: str) -> str:
        result = await self.agent.run(
            message,
            usage_limits=UsageLimits(
                request_limit=constants.MAX_MODEL_REQUESTS,
                tool_calls_limit=constants.MAX_TOOL_CALLS,
            ),
            timeout=constants.RUN_TIMEOUT_SECONDS,
        )
        return result.output

    async def run_turn(self, message: str, context: ConversationContext,
                       forget: Callable[[], Awaitable[str]], recall: ArchiveRecall, facts: FactCleanup) -> AgentRunResult[str]:
        return await self.agent.run(
            message, message_history=context.messages,
            instructions=CONVERSATION_INSTRUCTIONS,
            capabilities=[ConversationCompaction(context)],
            toolsets=[FunctionToolset(tools=[Tool(forget, name='forget_conversation'), Tool(recall.recall_conversation), Tool(FactEditor(self.model, facts).edit_conversation_fact)])],
            usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS,
                                     tool_calls_limit=constants.MAX_TOOL_CALLS),
            model_settings={'max_tokens': MAX_OUTPUT_TOKENS},
            timeout=constants.RUN_TIMEOUT_SECONDS,
        )

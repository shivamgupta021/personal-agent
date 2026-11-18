"""One research agent; Pydantic AI owns the model/tool loop."""

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from research import constants
from research.tools.firecrawl import Firecrawl
from research.prompts import RESEARCH_INSTRUCTIONS


class ResearchAgent:
    def __init__(self, model: Model, firecrawl: Firecrawl) -> None:
        self.agent: Agent[None, str] = Agent(
            model,
            instructions=RESEARCH_INSTRUCTIONS,
            name='research',
            retries=constants.AGENT_VALIDATION_RETRIES,
            tools=[firecrawl.search, firecrawl.read_page],
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

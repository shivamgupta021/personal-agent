"""One research agent; Pydantic AI owns the model/tool loop."""

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.tools import Tool
from pydantic_ai.usage import UsageLimits

from personal_agent import constants
from personal_agent.firecrawl import Firecrawl
from personal_agent.prompts import RESEARCH_INSTRUCTIONS
from personal_agent.telemetry import tracing


class ResearchAgent:
    def __init__(self, model: Model, firecrawl: Firecrawl) -> None:
        self.agent: Agent[None, str] = Agent(
            model,
            instructions=RESEARCH_INSTRUCTIONS,
            name='research',
            retries=constants.AGENT_VALIDATION_RETRIES,
            # Serialize tool execution to keep local service load predictable.
            tools=[Tool(firecrawl.search, sequential=True), Tool(firecrawl.read_page, sequential=True)],
        )
        # Automatic instrumentation may capture content; tracing exports only our allowlist.
        self.agent.instrument = False

    async def run(self, message: str) -> str:
        async with tracing.observe() as trace:
            result = await self.agent.run(
                message,
                usage=trace.usage,
                usage_limits=UsageLimits(
                    request_limit=constants.MAX_MODEL_REQUESTS,
                    tool_calls_limit=constants.MAX_TOOL_CALLS,
                ),
                timeout=constants.RUN_TIMEOUT_SECONDS,
            )
            return result.output

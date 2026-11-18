"""Bounded query planners with separate prompts and validated output contracts."""

from enum import StrEnum

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from research import constants
from research.planning.prompts import COMPLEX_QUERY_INSTRUCTIONS, SIMPLE_QUERY_INSTRUCTIONS
from research.planning.schema import ComplexSearchPlan, SearchPlan, SimpleSearchPlan


class PlanningMode(StrEnum):
    SIMPLE = 'simple'
    COMPLEX = 'complex'


class QueryPlanner:
    def __init__(self, model: Model, mode: PlanningMode = PlanningMode.SIMPLE) -> None:
        output_type: type[SearchPlan] = SimpleSearchPlan if mode == PlanningMode.SIMPLE else ComplexSearchPlan
        self.agent: Agent[None, SearchPlan] = Agent(
            model, name=f'{mode}_query_planner',
            instructions=SIMPLE_QUERY_INSTRUCTIONS if mode == PlanningMode.SIMPLE else COMPLEX_QUERY_INSTRUCTIONS,
            output_type=output_type, retries=constants.AGENT_VALIDATION_RETRIES,
        )

    async def run(
        self, message: str, usage: RunUsage | None = None, request_limit: int | None = None,
    ) -> SearchPlan:
        # Count child requests against the parent's budget, while bounding this invocation.
        limit = (usage.requests if usage else 0) + constants.PLANNER_MAX_MODEL_REQUESTS
        if request_limit is not None:
            limit = min(limit, request_limit)
        result = await self.agent.run(
            message, usage=usage,
            usage_limits=UsageLimits(request_limit=limit),
            timeout=constants.PLANNER_TIMEOUT_SECONDS,
        )
        return result.output

"""Delegate query planning and execute the plan without another routing round trip."""

from typing import Any

from pydantic_ai import RunContext

from research.planning.agent import QueryPlanner
from research.tools.firecrawl import Firecrawl


class ResearchSearch:
    def __init__(self, firecrawl: Firecrawl, simple: QueryPlanner, complex: QueryPlanner) -> None:
        self.firecrawl = firecrawl
        self.simple = simple
        self.complex = complex

    async def search_simple(self, ctx: RunContext[None], question: str) -> dict[str, Any]:
        """Delegate a single focused evidence need to the simple planner, then search its query."""
        return await self._search(ctx, question, self.simple)

    async def search_complex(self, ctx: RunContext[None], question: str) -> dict[str, Any]:
        """Delegate multiple distinct evidence needs to the complex planner, then search its queries."""
        return await self._search(ctx, question, self.complex)

    async def _search(self, ctx: RunContext[None], question: str, planner: QueryPlanner) -> dict[str, Any]:
        limit = ctx.usage_limits.request_limit if ctx.usage_limits else None
        plan = await planner.run(question, usage=ctx.usage, request_limit=limit)
        result = await self.firecrawl.search(plan.queries)
        return {'queries': plan.queries, **result}

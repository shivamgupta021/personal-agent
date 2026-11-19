"""Compose the real research dependencies for one application request."""

from collections.abc import Mapping, Awaitable, Callable

import httpx2
from django.conf import settings
from pydantic_ai.models import Model
from conversations.context import ConversationContext
from pydantic_ai.run import AgentRunResult

from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl
from subscriptions.constants import DEFAULT_MODEL
from subscriptions.service import SubscriptionService


class ResearchService:
    def __init__(self, subscription: SubscriptionService) -> None:
        self.subscription = subscription

    async def run(self, message: str, model_name: str = DEFAULT_MODEL, *, use_query_planners: bool = False) -> str:
        headers = {'Authorization': f'Bearer {settings.FIRECRAWL_API_KEY}'} if settings.FIRECRAWL_API_KEY else {}
        names = {'main': model_name}
        if use_query_planners:
            names.update(simple=settings.SIMPLE_QUERY_MODEL, complex=settings.COMPLEX_QUERY_MODEL)
        async with (
            self.subscription.models(names) as models,
            httpx2.AsyncClient(base_url=settings.FIRECRAWL_BASE_URL,
                              headers=headers, follow_redirects=False) as client,
        ):
            return await self.run_with_dependencies(
                message, models, Firecrawl(client), use_query_planners=use_query_planners,
            )

    @staticmethod
    async def run_with_dependencies(
        message: str, models: Mapping[str, Model], firecrawl: Firecrawl, *, use_query_planners: bool = False,
    ) -> str:
        """Share agent invocation with evaluations that supply controlled external boundaries."""
        return await ResearchAgent(
            models['main'], firecrawl, models.get('simple'), models.get('complex'),
            use_query_planners=use_query_planners,
        ).run(message)

    async def run_turn(self, message: str, context: ConversationContext,
                       forget: Callable[[], Awaitable[str]]) -> AgentRunResult[str]:
        headers = {'Authorization': f'Bearer {settings.FIRECRAWL_API_KEY}'} if settings.FIRECRAWL_API_KEY else {}
        async with (
            self.subscription.model(DEFAULT_MODEL) as model,
            httpx2.AsyncClient(base_url=settings.FIRECRAWL_BASE_URL,
                              headers=headers, follow_redirects=False) as client,
        ):
            return await ResearchAgent(model, Firecrawl(client)).run_turn(message, context, forget)

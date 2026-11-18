"""Compose the real research dependencies for one application request."""

import httpx2
from django.conf import settings

from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl
from subscriptions.service import SubscriptionService


class ResearchService:
    def __init__(self, subscription: SubscriptionService) -> None:
        self.subscription = subscription

    async def run(self, message: str, model_name: str) -> str:
        headers = {'Authorization': f'Bearer {settings.FIRECRAWL_API_KEY}'} if settings.FIRECRAWL_API_KEY else {}
        async with (
            self.subscription.model(model_name) as model,
            httpx2.AsyncClient(base_url=settings.FIRECRAWL_BASE_URL,
                              headers=headers, follow_redirects=False) as client,
        ):
            return await ResearchAgent(model, Firecrawl(client)).run(message)

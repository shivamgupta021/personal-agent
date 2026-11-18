"""Subscription lifetime for an independently runnable query planner."""

from research.planning.agent import PlanningMode, QueryPlanner
from research.planning.schema import SearchPlan
from subscriptions.constants import DEFAULT_MODEL
from subscriptions.service import SubscriptionService


class PlanningService:
    def __init__(self, subscription: SubscriptionService) -> None:
        self.subscription = subscription

    async def run(self, message: str, model_name: str = DEFAULT_MODEL, mode: PlanningMode = PlanningMode.SIMPLE) -> SearchPlan:
        async with self.subscription.model(model_name) as model:
            return await QueryPlanner(model, mode).run(message)

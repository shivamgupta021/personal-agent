"""Subscription operations and authenticated model construction."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

from subscriptions import constants
from subscriptions.auth import SubscriptionSession


class SubscriptionService:
    def __init__(self, state_dir: Path) -> None:
        self.session = SubscriptionSession(state_dir)

    async def login(self) -> None:
        with self.session.locked():
            await self.session.login()

    async def list_models(self) -> list[str]:
        async with self._client() as client:
            catalog = await client.get('/models', cast_to=dict[str, Any])
            return [f"{model['slug']}  {model.get('display_name', '')}"
                    for model in catalog.get('models', []) if model.get('visibility') == 'list']

    @asynccontextmanager
    async def model(self, name: str = constants.DEFAULT_MODEL) -> AsyncIterator[OpenAIResponsesModel]:
        async with self.models({'main': name}) as models:
            yield models['main']

    @asynccontextmanager
    async def models(self, names: dict[str, str]) -> AsyncIterator[dict[str, OpenAIResponsesModel]]:
        """Share one authenticated client and session lock across the agent tree."""
        async with self._client() as client:
            provider = OpenAIChatGPTProvider(openai_client=client)
            yield {
                role: OpenAIResponsesModel(
                    name, provider=provider,
                    settings=OpenAIResponsesModelSettings(
                        openai_store=False, timeout=constants.MODEL_TIMEOUT_SECONDS,
                    ),
                ) for role, name in names.items()
            }

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[AsyncOpenAI]:
        with self.session.locked():
            await self.session.credentials.load()
            async with self.session.provider().client.with_options(
                max_retries=constants.MODEL_HTTP_RETRIES, timeout=constants.MODEL_TIMEOUT_SECONDS,
            ) as client:
                yield client

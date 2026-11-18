import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

from personal_agent import constants
from personal_agent.auth import SubscriptionSession
from personal_agent.errors import user_error_message
from personal_agent.firecrawl import Firecrawl
from personal_agent.logging_setup import configure_logging
from personal_agent.research import ResearchAgent
from personal_agent.telemetry import tracing


logger = logging.getLogger('personal_agent.cli')


class ResearchApp:
    """Connect subscription authentication and research to the terminal."""

    async def run_command(self, command: str, directory: Path, model_name: str | None,
                          message: str | None = None) -> int:
        session = SubscriptionSession(directory)
        with session.locked():
            if command == "login":
                await session.login()
                return 0
            await session.credentials.load()
            async with session.provider().client.with_options(
                max_retries=constants.MODEL_HTTP_RETRIES, timeout=constants.MODEL_TIMEOUT_SECONDS,
            ) as client:
                if command == "models":
                    catalog = await client.get("/models", cast_to=dict[str, Any])
                    for model in catalog.get("models", []):
                        if model.get("visibility") == "list":
                            print(f"{model['slug']}  {model.get('display_name', '')}")
                    return 0
                if model_name is None or message is None:
                    raise ValueError('Research requires a model and question')
                await self.research(client, model_name, message)
        return 0

    async def research(self, client: AsyncOpenAI, model_name: str, message: str) -> None:
        firecrawl_key = os.getenv('FIRECRAWL_API_KEY')
        headers = {'Authorization': f'Bearer {firecrawl_key}'} if firecrawl_key else {}
        async with httpx2.AsyncClient(
            base_url=os.getenv('FIRECRAWL_BASE_URL', constants.FIRECRAWL_BASE_URL),
            headers=headers, follow_redirects=False,
        ) as web_client:
            model = OpenAIResponsesModel(
                model_name, provider=OpenAIChatGPTProvider(openai_client=client),
                settings=OpenAIResponsesModelSettings(openai_store=False, timeout=constants.MODEL_TIMEOUT_SECONDS,
                                                     parallel_tool_calls=False),
            )
            logger.info('Using ChatGPT subscription and local Firecrawl')
            answer = await ResearchAgent(model, Firecrawl(web_client)).run(message)
            print(answer)

    async def start(self, command: str, directory: Path, model_name: str | None,
                    message: str | None) -> int:
        if command == 'research':
            async with tracing.startup():
                return await self.run_command(command, directory, model_name, message)
        return await self.run_command(command, directory, model_name, message)

    def run(self) -> int:
        configure_logging()
        os.environ.setdefault('PYDANTIC_AI_NO_BANNER', '1')
        parser = argparse.ArgumentParser(description="Research a public topic with local Firecrawl")
        parser.add_argument("command", choices=["login", "models", "research"])
        parser.add_argument("message", nargs="?", help="Public research question (research only)")
        parser.add_argument("--model", help="Account-specific slug from the models command")
        parser.add_argument("--state-dir", type=Path, default=Path(".local/chatgpt"))
        args = parser.parse_args()
        if args.command == "research" and not args.model:
            parser.error("research requires --model; use models to discover available slugs")
        if args.command == 'research' and (
            not args.message or not args.message.strip()
            or len(args.message) > constants.MAX_MESSAGE_CHARACTERS
        ):
            parser.error(f'research requires a public question of 1–{constants.MAX_MESSAGE_CHARACTERS} characters')
        if args.command != 'research' and args.message is not None:
            parser.error('a question is accepted only by research')
        try:
            return asyncio.run(self.start(args.command, args.state_dir, args.model, args.message))
        except KeyboardInterrupt:
            logger.info("Cancelled")
            return 130
        except Exception as error:
            logger.error(user_error_message(error))
            return 1


def main() -> int:
    return ResearchApp().run()


if __name__ == "__main__":
    raise SystemExit(main())

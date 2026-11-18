import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

import httpx2
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

from personal_agent.firecrawl import Firecrawl
from personal_agent.research import ResearchAgent
from personal_agent.telemetry import LangfuseTrace
from personal_agent.auth import SubscriptionSession
from personal_agent.loop import Agent
from personal_agent.models import OfflineModel
from personal_agent.subscription import ResponsesModel

DEMO_MESSAGE = "Look up the synthetic order demo-001 and tell me its amount and currency."


class LearningApp:
    """Compose the lesson components and present their results in the terminal."""

    async def run_command(self, command: str, directory: Path, model_name: str | None,
                          message: str | None = None) -> int:
        if command == "demo":
            print("OFFLINE: scripted responses, no network or model usage. Synthetic data only.")
            await Agent(OfflineModel()).run(DEMO_MESSAGE)
        else:
            session = SubscriptionSession(directory)
            with session.locked():
                if command == "login":
                    await session.login()
                    return 0
                await session.credentials.load()
                async with session.provider().client.with_options(max_retries=0, timeout=30) as client:
                    if command == "models":
                        catalog = await client.get("/models", cast_to=dict[str, Any])
                        for model in catalog.get("models", []):
                            if model.get("visibility") == "list":
                                print(f"{model['slug']}  {model.get('display_name', '')}")
                        return 0
                    assert model_name is not None
                    if command == "research":
                        assert message is not None
                        await self.research(client, model_name, message)
                        return 0
                    print("LIVE: ChatGPT subscription usage. Synthetic data only; no external actions.")
                    await Agent(ResponsesModel(client, model_name)).run(DEMO_MESSAGE)
        print("\nStopped: answered")
        return 0

    async def research(self, client: AsyncOpenAI, model_name: str, message: str) -> None:
        public_key = os.getenv('LANGFUSE_PUBLIC_KEY')
        secret_key = os.getenv('LANGFUSE_SECRET_KEY')
        if bool(public_key) != bool(secret_key):
            raise RuntimeError('Set both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.')
        firecrawl_key = os.getenv('FIRECRAWL_API_KEY')
        headers = {'Authorization': f'Bearer {firecrawl_key}'} if firecrawl_key else {}
        async with (
            httpx2.AsyncClient(base_url=os.getenv('FIRECRAWL_BASE_URL', 'http://127.0.0.1:3002'),
                              headers=headers, follow_redirects=False) as web_client,
            httpx2.AsyncClient(base_url=os.getenv('LANGFUSE_BASE_URL', 'http://127.0.0.1:3000'),
                              auth=(public_key, secret_key) if public_key and secret_key else None,
                              follow_redirects=False) as trace_client,
        ):
            model = OpenAIResponsesModel(
                model_name, provider=OpenAIChatGPTProvider(openai_client=client),
                settings=OpenAIResponsesModelSettings(openai_store=False, timeout=45,
                                                     parallel_tool_calls=False),
            )
            tracing = LangfuseTrace(trace_client) if public_key and secret_key else None
            print('Research: ChatGPT subscription + local Firecrawl. Public topics only.')
            print('Tracing: metadata only.' if tracing else 'Tracing: disabled (no project keys).')
            answer = await ResearchAgent(model, Firecrawl(web_client), tracing=tracing).run(message)
            print(f'\n{answer}\n\nStopped: answered')

    def run(self) -> int:
        os.environ.setdefault('PYDANTIC_AI_NO_BANNER', '1')
        parser = argparse.ArgumentParser(description="Learn the loop or research a public topic with local Firecrawl")
        parser.add_argument("command", choices=["demo", "login", "models", "live", "research"])
        parser.add_argument("message", nargs="?", help="Public research question (research only)")
        parser.add_argument("--model", help="Account-specific slug from the models command")
        parser.add_argument("--state-dir", type=Path, default=Path(".local/chatgpt"))
        args = parser.parse_args()
        if args.command in {"live", "research"} and not args.model:
            parser.error("live/research requires --model; use models to discover available slugs")
        if args.command == 'research' and (not args.message or not args.message.strip() or len(args.message) > 8000):
            parser.error('research requires a public question of 1–8000 characters')
        if args.command != 'research' and args.message is not None:
            parser.error('a question is accepted only by research')
        try:
            return asyncio.run(self.run_command(args.command, args.state_dir, args.model, args.message))
        except KeyboardInterrupt:
            print("Cancelled.", file=sys.stderr)
            return 130
        except Exception as error:
            # Only our own fixed messages may be printed; SDK exceptions can contain secrets.
            safe_messages = {
                "execution_limit", "research_failed",
                "Set both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.",
                "request_limit", "tool_limit", "timeout", "provider_error", "invalid_response",
                "Run the login command first.",
                "Another command is using this subscription session.",
                "Could not open the browser. Run login from a desktop terminal.",
                "Sign-in completed, but plan usage was not granted. Run login again.",
            }
            message = str(error) if type(error) is RuntimeError and str(error) in safe_messages else (
                "Command failed. Check connectivity and subscription access; retry login if needed. "
                "Provider error details are hidden to protect credentials."
            )
            print(message, file=sys.stderr)
            return 1


def main() -> int:
    return LearningApp().run()


if __name__ == "__main__":
    raise SystemExit(main())

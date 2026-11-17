import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from personal_agent.auth import SubscriptionSession
from personal_agent.loop import Agent
from personal_agent.models import OfflineModel
from personal_agent.subscription import ResponsesModel

DEMO_MESSAGE = "Look up the synthetic order demo-001 and tell me its amount and currency."


class LearningApp:
    """Compose the lesson components and present their results in the terminal."""

    async def run_command(self, command: str, directory: Path, model_name: str | None) -> int:
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
                    print("LIVE: ChatGPT subscription usage. Synthetic data only; no external actions.")
                    await Agent(ResponsesModel(client, model_name)).run(DEMO_MESSAGE)
        print("\nStopped: answered")
        return 0

    def run(self) -> int:
        parser = argparse.ArgumentParser(description="Milestone 1: inspect a synthetic model/tool loop")
        parser.add_argument("command", choices=["demo", "login", "models", "live"])
        parser.add_argument("--model", help="Account-specific slug from the models command")
        parser.add_argument("--state-dir", type=Path, default=Path(".local/chatgpt"))
        args = parser.parse_args()
        if args.command == "live" and not args.model:
            parser.error("live requires --model; use models to discover available slugs")
        try:
            return asyncio.run(self.run_command(args.command, args.state_dir, args.model))
        except KeyboardInterrupt:
            print("Cancelled.", file=sys.stderr)
            return 130
        except Exception as error:
            # Only our own fixed messages may be printed; SDK exceptions can contain secrets.
            safe_messages = {
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

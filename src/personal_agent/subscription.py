"""Responses API transport. The loop itself lives in loop.py."""

from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.responses import ResponseInputParam, ToolParam


class ResponsesModel:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self.client = client
        self.name = model

    async def respond(
        self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        async with await self.client.responses.create(
            model=self.name,
            input=cast(ResponseInputParam, history),
            tools=cast(list[ToolParam], tools),
            include=["reasoning.encrypted_content"],
            store=False,
            stream=True,
        ) as stream:
            async for event in stream:
                if event.type == "response.completed":
                    return {
                        "status": event.response.status,
                        "output": [item.model_dump(mode="json", exclude_none=True)
                                   for item in event.response.output],
                    }
                if event.type in {"response.failed", "response.incomplete", "error"}:
                    raise RuntimeError("The response did not complete.")
        raise RuntimeError("The stream ended before response.completed.")

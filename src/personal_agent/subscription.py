"""Responses API transport. The loop itself lives in loop.py."""

from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.responses import ResponseInputParam, ToolParam


class ResponsesModel:
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self.client = client
        self.model = model

    async def respond(
        self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        async with self.client.responses.stream(
            model=self.model,
            instructions=("You are running a synthetic learning exercise. Use demo.get_demo_order "
                          "to look up demo-001 before answering. Do not invent order facts. "
                          "Never claim to have sent a complaint or requested a real refund."),
            input=cast(ResponseInputParam, history),
            tools=cast(list[ToolParam], tools),
            include=["reasoning.encrypted_content"],
            store=False,
        ) as stream:
            response = await stream.get_final_response()
        return response.model_dump(mode="json", exclude_none=True)

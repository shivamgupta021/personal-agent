import asyncio
import json
from typing import Any

import httpx2
from openai import AsyncOpenAI

from personal_agent.loop import run_turn
from personal_agent.subscription import ResponsesModel


def test_public_loop_consumes_completed_stream_with_full_tool_history() -> None:
    requests: list[dict[str, Any]] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert body["store"] is False
        assert body["stream"] is True
        assert body["tools"][0]["type"] == "namespace"
        output: list[dict[str, Any]]
        if len(requests) == 1:
            output = [{"type": "function_call", "id": "fc_1", "status": "completed",
                       "call_id": "c1", "namespace": "demo", "name": "get_demo_order",
                       "arguments": '{"order_id":"demo-001"}'}]
        else:
            assert body["input"][-1]["call_id"] == "c1"
            assert json.loads(body["input"][-1]["output"])["amount"] == 1200
            output = [{"type": "message", "id": "msg_1", "role": "assistant",
                       "status": "completed", "content": [{"type": "output_text",
                       "text": "The order cost INR 1200.", "annotations": []}]}]
        event: dict[str, Any] = {"type": "response.completed", "sequence_number": 0,
                 "response": {"id": "resp_1", "object": "response", "created_at": 1,
                              "status": "completed", "model": "test-model", "output": output}}
        created = {"type": "response.created", "sequence_number": 0,
                   "response": {**event["response"], "status": "in_progress", "output": []}}
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
            text=f"data: {json.dumps(created)}\n\ndata: {json.dumps(event)}\n\n")

    async def exercise() -> None:
        async with AsyncOpenAI(api_key="synthetic-token", max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))) as client:
            result = await run_turn("What did demo-001 cost?", ResponsesModel(client, "test-model"))
            assert result.answer == "The order cost INR 1200."

    asyncio.run(exercise())

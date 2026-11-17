"""Model boundary: a scripted teaching model and the shared response shape."""

import json
from typing import Any, Protocol


class Model(Protocol):
    name: str

    async def respond(
        self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


class OfflineModel:
    """A scripted model stand-in, not AI inference."""

    name = "scripted-offline"

    async def respond(
        self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if history[-1].get("type") == "function_call_output":
            order = json.loads(history[-1]["output"])
            return {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": f"The synthetic order cost {order['currency']} {order['amount']}."}
            ]}]}
        return {"status": "completed", "output": [{"type": "function_call",
            "namespace": "demo", "name": "get_demo_order", "call_id": "demo-lookup-1",
            "arguments": '{"order_id":"demo-001"}'}]}


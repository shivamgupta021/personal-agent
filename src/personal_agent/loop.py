"""Start here: ask the model, run its tool request, send the result back."""

import asyncio
import json
from copy import deepcopy
from typing import Any

from personal_agent.models import Model


class Agent:
    """One teaching agent, one read-only tool, and a bounded loop."""

    def __init__(
        self, model: Model, *, max_requests: int = 4,
        max_tool_calls: int = 3, request_timeout: float = 30,
    ) -> None:
        self.model = model
        self.max_requests = max_requests
        self.max_tool_calls = max_tool_calls
        self.request_timeout = request_timeout

    async def run(self, message: str) -> str:
        # 1. Start a fresh conversation for this run.
        messages: list[dict[str, Any]] = [
            {"role": "developer", "content": (
                "This is a synthetic learning exercise. Use demo.get_demo_order to look up "
                "demo-001 before answering. Do not invent order facts or claim real actions."
            )},
            {"role": "user", "content": message},
        ]
        tools = self.tool_schema()
        tool_calls = 0

        for _ in range(self.max_requests):
            # 2. Ask the model, showing exactly which instructions and tools it receives.
            self.show("request", {"model": self.model.name, "input": messages, "tools": tools})
            try:
                async with asyncio.timeout(self.request_timeout):
                    response = await self.model.respond(deepcopy(messages), deepcopy(tools))
            except TimeoutError:
                raise RuntimeError("timeout") from None
            except Exception:
                # Never expose a provider error body: it can contain credentials.
                raise RuntimeError("provider_error") from None

            output = response.get("output")
            if response.get("status") != "completed" or not isinstance(output, list):
                raise RuntimeError("invalid_response")
            if any(not isinstance(item, dict) or "type" not in item for item in output):
                raise RuntimeError("invalid_response")
            self.show("response", {"status": "completed", "output": output})
            messages.extend(output)
            calls = [item for item in output if item["type"] == "function_call"]

            # 3. If it answered instead of requesting a tool, we're done.
            if not calls:
                answer = self.read_answer(output)
                self.show("answer", {"text": answer})
                return answer

            # 4. Python validates and executes each tool request.
            for call in calls:
                if not isinstance(call.get("call_id"), str) or not call["call_id"]:
                    raise RuntimeError("invalid_response")
                if tool_calls >= self.max_tool_calls:
                    raise RuntimeError("tool_limit")
                tool_calls += 1
                self.show("tool_call", call)
                result = self.get_demo_order(call)

                # 5. Give the result back to the model on the next iteration.
                tool_message = {"type": "function_call_output", "call_id": call["call_id"],
                                "output": json.dumps(result)}
                messages.append(tool_message)
                self.show("tool_result", tool_message)

        raise RuntimeError("request_limit")

    def get_demo_order(self, call: dict[str, Any]) -> dict[str, Any]:
        """Validate the request before returning our one invented order."""
        if call.get("name") != "get_demo_order" or call.get("namespace") != "demo":
            return {"error": "Only demo.get_demo_order is allowed."}
        try:
            arguments = json.loads(call.get("arguments", ""))
        except (ValueError, TypeError):
            return {"error": "Arguments must be a JSON object with order_id=demo-001."}
        if arguments != {"order_id": "demo-001"}:
            return {"error": "Only the synthetic order demo-001 is available; no extra arguments."}
        return {"order_id": "demo-001", "currency": "INR", "amount": 1200,
                "item": "Synthetic headphones", "synthetic": True}

    def tool_schema(self) -> list[dict[str, Any]]:
        """Describe to the model how it can request the method above."""
        return [{"type": "namespace", "name": "demo",
                 "description": "Read-only synthetic orders for a learning exercise.",
                 "tools": [{"type": "function", "name": "get_demo_order",
                            "description": "Look up the synthetic order demo-001.", "strict": True,
                            "parameters": {"type": "object", "properties": {
                                "order_id": {"type": "string", "enum": ["demo-001"]}
                            }, "required": ["order_id"], "additionalProperties": False}}]}]

    def read_answer(self, output: list[dict[str, Any]]) -> str:
        """Extract text from the API's message envelope."""
        try:
            text = "\n".join(
                part["text"] for item in output if item["type"] == "message"
                for part in item.get("content", []) if part.get("type") == "output_text"
            )
        except (KeyError, TypeError, AttributeError):
            raise RuntimeError("invalid_response") from None
        if not text.strip():
            raise RuntimeError("invalid_response")
        return text

    def show(self, step: str, data: dict[str, Any]) -> None:
        """Print immediately; no event framework or stored transcript."""
        print(f"\n[{step}]\n{json.dumps(data, indent=2)}", flush=True)

"""The model requests a tool; Python runs it and sends the result back."""

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

INSTRUCTIONS = (
    "You are running a synthetic learning exercise. Use demo.get_demo_order "
    "to look up demo-001 before answering. Do not invent order facts. "
    "Never claim to have sent a complaint or requested a real refund."
)

TOOLS: list[dict[str, Any]] = [{
    "type": "namespace", "name": "demo",
    "description": "Read-only synthetic orders for a learning exercise.",
    "tools": [{
        "type": "function", "name": "get_demo_order",
        "description": "Look up the synthetic order demo-001. No real orders exist here.",
        "strict": True,
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "enum": ["demo-001"]}
        }, "required": ["order_id"], "additionalProperties": False},
    }],
}]


class Model(Protocol):
    name: str

    async def respond(
        self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Limits:
    max_requests: int = 4
    max_tool_calls: int = 3
    request_timeout: float = 30


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict[str, Any]


@dataclass(frozen=True)
class RunResult:
    answer: str | None
    stop_reason: str
    events: tuple[Event, ...]


def execute_tool(call: dict[str, Any]) -> dict[str, Any]:
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


async def run_turn(message: str, model: Model, *, limits: Limits = Limits()) -> RunResult:
    history: list[dict[str, Any]] = [
        {"role": "developer", "content": INSTRUCTIONS},
        {"role": "user", "content": message},
    ]
    events: list[Event] = []
    tool_calls = 0

    def stop(reason: str) -> RunResult:
        events.append(Event("stopped", {"reason": reason}))
        return RunResult(None, reason, tuple(events))

    for _ in range(limits.max_requests):
        events.append(Event("request", deepcopy({"model": model.name, "input": history, "tools": TOOLS})))
        try:
            async with asyncio.timeout(limits.request_timeout):
                response = await model.respond(deepcopy(history), deepcopy(TOOLS))
        except TimeoutError:
            return stop("timeout")
        except Exception:
            # Provider errors can contain tokens or request bodies. Never echo them.
            return stop("provider_error")
        events.append(Event("response", deepcopy(response)))
        output = response.get("output")
        if response.get("status") != "completed" or not isinstance(output, list):
            return stop("invalid_response")
        if any(not isinstance(item, dict) or "type" not in item for item in output):
            return stop("invalid_response")
        history.extend(output)
        calls = [item for item in output if item["type"] == "function_call"]
        if any(not isinstance(call.get("call_id"), str) or not call["call_id"] for call in calls):
            return stop("invalid_response")
        if not calls:
            answer = "\n".join(
                part["text"] for item in output if item["type"] == "message"
                for part in item.get("content", []) if part.get("type") == "output_text"
            )
            if not answer.strip():
                return stop("invalid_response")
            events.append(Event("answer", {"text": answer}))
            return RunResult(answer, "answered", tuple(events))
        for call in calls:
            if tool_calls >= limits.max_tool_calls:
                return stop("tool_limit")
            tool_calls += 1
            events.append(Event("tool_call", deepcopy(call)))
            result = execute_tool(call)
            tool_output = {"type": "function_call_output", "call_id": call["call_id"],
                           "output": json.dumps(result)}
            history.append(tool_output)
            events.append(Event("tool_result", deepcopy(tool_output)))
    return stop("request_limit")

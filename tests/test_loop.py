import asyncio
import json
from typing import Any

import pytest

from personal_agent.loop import AgentLoop, Limits


class OrderModel:
    name = "controlled-order-model"

    async def respond(
        self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if history[-1].get("type") == "function_call_output":
            assert '"amount": 1200' in history[-1]["output"]
            assert history[-1]["call_id"] == "lookup-1"
            return {
                "status": "completed",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "The demo order cost INR 1200."}
                ]}],
            }
        return {
            "status": "completed",
            "output": [{"type": "function_call", "call_id": "lookup-1",
                        "namespace": "demo", "name": "get_demo_order",
                        "arguments": '{"order_id":"demo-001"}'}],
        }


def test_tool_result_reaches_model_and_steps_are_visible() -> None:
    result = asyncio.run(AgentLoop(OrderModel()).run_turn("What did demo-001 cost?"))
    assert result.answer == "The demo order cost INR 1200."
    assert result.stop_reason == "answered"
    assert [event.kind for event in result.events] == [
        "request", "response", "tool_call", "tool_result", "request", "response", "answer"
    ]


def test_reusing_an_agent_loop_does_not_share_history_or_budgets_between_turns() -> None:
    async def exercise() -> None:
        agent = AgentLoop(OrderModel(), limits=Limits(max_requests=2, max_tool_calls=1))
        first = await agent.run_turn("First synthetic question")
        second = await agent.run_turn("Second synthetic question")
        assert first.stop_reason == second.stop_reason == "answered"
        assert second.events[0].data["input"][-1] == {
            "role": "user", "content": "Second synthetic question",
        }
        assert "First synthetic question" not in repr(second)

    asyncio.run(exercise())


@pytest.mark.parametrize("name,namespace,arguments", [
    ("send_email", "demo", "{}"),
    ("get_demo_order", "other", '{"order_id":"demo-001"}'),
    ("get_demo_order", "demo", "not json"),
    ("get_demo_order", "demo", '{"order_id":"real-order"}'),
    ("get_demo_order", "demo", '{"order_id":"demo-001","extra":true}'),
    ("get_demo_order", "demo", '[]'),
])
def test_invalid_tool_requests_return_an_error_not_order_data(
    name: str, namespace: str, arguments: str
) -> None:
    class InvalidModel:
        name = "controlled-invalid-model"

        async def respond(
            self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> dict[str, Any]:
            if history[-1].get("type") == "function_call_output":
                result = json.loads(history[-1]["output"])
                assert "error" in result
                assert "amount" not in result
                return {"status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "I could not perform that lookup."}
                ]}]}
            return {"status": "completed", "output": [{"type": "function_call",
                "call_id": "bad-1", "namespace": namespace, "name": name,
                "arguments": arguments}]}

    result = asyncio.run(AgentLoop(InvalidModel()).run_turn("Find an order"))
    assert result.answer == "I could not perform that lookup."


@pytest.mark.parametrize("limits,reason,executed", [
    (Limits(max_requests=2, max_tool_calls=5), "request_limit", 2),
    (Limits(max_requests=5, max_tool_calls=1), "tool_limit", 1),
])
def test_repeated_tool_requests_stop_at_the_budget(
    limits: Limits, reason: str, executed: int
) -> None:
    class RepeatingModel:
        name = "controlled-repeating-model"

        async def respond(
            self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> dict[str, Any]:
            return {"status": "completed", "output": [{"type": "function_call",
                "call_id": f"lookup-{len(history)}", "name": "get_demo_order",
                "namespace": "demo", "arguments": '{"order_id":"demo-001"}'}]}

    result = asyncio.run(AgentLoop(RepeatingModel(), limits=limits).run_turn("Look up the order"))
    assert result.answer is None
    assert result.stop_reason == reason
    assert sum(event.kind == "tool_result" for event in result.events) == executed
    assert result.events[-1].kind == "stopped"


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "output": []},
    {"status": "failed", "output": []},
    {"status": "completed", "output": []},
    {"status": "completed", "output": [{"type": "function_call", "name": "get_demo_order"}]},
    {"status": "completed", "output": None},
])
def test_unusable_response_is_not_reported_as_an_answer(response: dict[str, Any]) -> None:
    class UnusableModel:
        name = "controlled-unusable-model"

        async def respond(
            self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> dict[str, Any]:
            return response

    result = asyncio.run(AgentLoop(UnusableModel()).run_turn("Look up the order"))
    assert result.answer is None
    assert result.stop_reason == "invalid_response"
    assert not any(event.kind == "tool_result" for event in result.events)


@pytest.mark.parametrize("timeout", [True, False])
def test_provider_failure_stops_without_exposing_exception_content(timeout: bool) -> None:
    class FailingModel:
        name = "controlled-failing-model"

        async def respond(
            self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> dict[str, Any]:
            if timeout:
                await asyncio.sleep(1)
            raise RuntimeError("secret-token-in-provider-error")

    result = asyncio.run(AgentLoop(FailingModel(), limits=Limits(request_timeout=0.01)).run_turn("Look up the order"))
    assert result.stop_reason == ("timeout" if timeout else "provider_error")
    assert "secret-token" not in repr(result)
    assert result.answer is None

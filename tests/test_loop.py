import asyncio
import json
from typing import Any

import pytest

from personal_agent.loop import Agent


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


def test_tool_result_reaches_model_and_steps_are_visible(capsys: pytest.CaptureFixture[str]) -> None:
    answer = asyncio.run(Agent(OrderModel()).run("What did demo-001 cost?"))
    assert answer == "The demo order cost INR 1200."
    output = capsys.readouterr().out
    assert [line for line in output.splitlines() if line.startswith("[")] == [
        "[request]", "[response]", "[tool_call]", "[tool_result]", "[request]", "[response]", "[answer]"
    ]


def test_reusing_an_agent_does_not_share_history_or_budgets(capsys: pytest.CaptureFixture[str]) -> None:
    async def exercise() -> None:
        agent = Agent(OrderModel(), max_requests=2, max_tool_calls=1)
        first = await agent.run("First synthetic question")
        capsys.readouterr()
        second = await agent.run("Second synthetic question")
        assert first == second == "The demo order cost INR 1200."
        output = capsys.readouterr().out
        assert "Second synthetic question" in output
        assert "First synthetic question" not in output

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

    answer = asyncio.run(Agent(InvalidModel()).run("Find an order"))
    assert answer == "I could not perform that lookup."


@pytest.mark.parametrize("requests,tools,reason,executed", [
    (2, 5, "request_limit", 2),
    (5, 1, "tool_limit", 1),
])
def test_repeated_tool_requests_stop_at_the_budget(
    requests: int, tools: int, reason: str, executed: int, capsys: pytest.CaptureFixture[str]
) -> None:
    class RepeatingModel:
        name = "controlled-repeating-model"

        async def respond(
            self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> dict[str, Any]:
            return {"status": "completed", "output": [{"type": "function_call",
                "call_id": f"lookup-{len(history)}", "name": "get_demo_order",
                "namespace": "demo", "arguments": '{"order_id":"demo-001"}'}]}

    agent = Agent(RepeatingModel(), max_requests=requests, max_tool_calls=tools)
    with pytest.raises(RuntimeError, match=f"^{reason}$"):
        asyncio.run(agent.run("Look up the order"))
    output = capsys.readouterr().out
    assert output.count("[tool_result]") == executed
    assert "[answer]" not in output


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "output": []},
    {"status": "failed", "output": []},
    {"status": "completed", "output": []},
    {"status": "completed", "output": [{"type": "function_call", "name": "get_demo_order"}]},
    {"status": "completed", "output": None},
])
def test_unusable_response_is_not_reported_as_an_answer(
    response: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    class UnusableModel:
        name = "controlled-unusable-model"

        async def respond(
            self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> dict[str, Any]:
            return response

    with pytest.raises(RuntimeError, match="^invalid_response$"):
        asyncio.run(Agent(UnusableModel()).run("Look up the order"))
    assert "[tool_result]" not in capsys.readouterr().out


@pytest.mark.parametrize("timeout", [True, False])
def test_provider_failure_stops_without_exposing_exception_content(
    timeout: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    class FailingModel:
        name = "controlled-failing-model"

        async def respond(
            self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> dict[str, Any]:
            if timeout:
                await asyncio.sleep(1)
            raise RuntimeError("secret-token-in-provider-error")

    reason = "timeout" if timeout else "provider_error"
    with pytest.raises(RuntimeError, match=f"^{reason}$") as error:
        asyncio.run(Agent(FailingModel(), request_timeout=0.01).run("Look up the order"))
    assert "secret-token" not in str(error.value) + capsys.readouterr().out

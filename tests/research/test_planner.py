import asyncio

import pytest

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research.planning.agent import PlanningMode, QueryPlanner


@pytest.mark.parametrize('mode,invalid,valid', [
    (PlanningMode.SIMPLE, ['one', 'two'], ['official policy']),
    (PlanningMode.COMPLEX, ['one'], ['official policy', 'official support']),
])
def test_planner_validates_output_and_has_no_web_tools(
    mode: PlanningMode, invalid: list[str], valid: list[str],
) -> None:
    calls = 0

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert not info.function_tools
        assert info.output_tools
        queries = invalid if calls == 1 else valid
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'queries': queries})])

    plan = asyncio.run(QueryPlanner(FunctionModel(model), mode).run('Find official policy'))
    assert plan.queries == valid
    assert calls == 2


def test_planner_does_not_carry_previous_questions_into_later_runs() -> None:
    histories: list[int] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        histories.append(len(messages))
        assert info.output_tools
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'queries': ['public policy']})])

    async def exercise() -> None:
        planner = QueryPlanner(FunctionModel(model))
        await planner.run('First topic')
        await planner.run('Second topic')

    asyncio.run(exercise())
    assert histories == [1, 1]

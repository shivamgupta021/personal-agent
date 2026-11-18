import asyncio
import json

import httpx2
import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research import constants
from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl


@pytest.mark.parametrize('route,queries', [
    ('simple', ['official policy']),
    ('complex', ['official policy', 'official support']),
])
def test_main_routes_to_independent_planner_and_searches_its_output(route: str, queries: list[str]) -> None:
    selected: list[str] = []
    searched: list[str] = []

    def planner(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert not info.function_tools
        assert info.output_tools
        selected.append(route)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'queries': queries})])

    def unselected(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError('Unselected planner must not run')

    def main(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert {tool.name for tool in info.function_tools} == {'search_simple', 'search_complex', 'read_page'}
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart(f'search_{route}', {'question': 'Research this policy'})])
        assert isinstance(returns[-1].content, dict)
        assert returns[-1].content.get('queries') == queries
        return ModelResponse(parts=[TextPart('Sources returned')])

    def web(request: httpx2.Request) -> httpx2.Response:
        searched.append(json.loads(request.content)['query'])
        return httpx2.Response(200, json={'success': True, 'data': {'web': []}})

    async def exercise() -> None:
        from_model = FunctionModel(planner)
        other_model = FunctionModel(unselected)
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            agent = ResearchAgent(
                FunctionModel(main), Firecrawl(client),
                from_model if route == 'simple' else other_model,
                from_model if route == 'complex' else other_model,
             use_query_planners=True)
            assert await agent.run('Research') == 'Sources returned'

    asyncio.run(exercise())
    assert selected == [route]
    assert searched == queries


def test_delegated_request_consumes_main_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, 'MAX_MODEL_REQUESTS', 2)
    main_calls = 0
    planner_calls = 0

    def main(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal main_calls
        main_calls += 1
        return ModelResponse(parts=[ToolCallPart('search_simple', {'question': 'Public policy'})])

    def planner(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal planner_calls
        planner_calls += 1
        assert info.output_tools
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'queries': ['official policy']})])

    def web(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={'success': True, 'data': {'web': []}})

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            with pytest.raises(UsageLimitExceeded):
                await ResearchAgent(FunctionModel(main), Firecrawl(client), FunctionModel(planner), use_query_planners=True).run('Research')

    asyncio.run(exercise())
    assert main_calls == 1
    assert planner_calls == 1

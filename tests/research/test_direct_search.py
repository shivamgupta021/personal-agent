import asyncio
import json

import httpx2
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl


def test_default_research_searches_directly_without_planner_calls() -> None:
    model_calls = 0
    requests: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        assert not info.output_tools
        assert {tool.name for tool in info.function_tools} == {'search', 'read_page'}
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not results:
            return ModelResponse(parts=[ToolCallPart('search', {'queries': ['official policy']})])
        if len(results) == 1:
            return ModelResponse(parts=[ToolCallPart('read_page', {'url': 'https://example.com/policy'})])
        assert '14 days' in str(results[-1].content)
        return ModelResponse(parts=[TextPart('The policy says 14 days.')])

    def web(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        if request.url.path == '/v2/search':
            assert json.loads(request.content)['query'] == 'official policy'
            return httpx2.Response(200, json={'success': True, 'data': {
                'web': [{'url': 'https://example.com/policy'}]}})
        return httpx2.Response(200, json={'success': True, 'data': {'markdown': 'Returns within 14 days.'}})

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            answer = await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('Find the official policy')
        assert answer == 'The policy says 14 days.'

    asyncio.run(exercise())
    assert model_calls == 3
    assert requests == ['/v2/search', '/v2/scrape']

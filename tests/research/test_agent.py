import asyncio
import json
import logging
from typing import Any

import httpx2
import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research.tools.firecrawl import Firecrawl
from research.agent import ResearchAgent
from research import constants


def test_research_searches_reads_and_answers_with_fresh_history(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger='research')
    requests: list[str] = []

    def web(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        if request.url.path == '/v2/search':
            assert json.loads(request.content)['query'] == 'Example returns policy'
            return httpx2.Response(200, json={'success': True, 'data': {'web': [
                {'url': 'https://example.com/returns', 'title': 'Returns', 'description': 'Official policy'}]}})
        return httpx2.Response(200, json={'success': True, 'data': {
            'markdown': 'Unused items may be returned within 14 days.',
            'metadata': {'sourceURL': 'https://example.com/returns'}}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not results:
            return ModelResponse(parts=[ToolCallPart('search', {'queries': ['Example returns policy']})])
        if len(results) == 1:
            assert 'https://example.com/returns' in str(results[0].content)
            return ModelResponse(parts=[ToolCallPart('read_page', {'url': 'https://example.com/returns'})])
        assert '14 days' in str(results[-1].content)
        assert 'retrieved_at' in str(results[-1].content)
        return ModelResponse(parts=[TextPart('Unused items: 14 days [source](https://example.com/returns).')])

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            agent = ResearchAgent(FunctionModel(model), Firecrawl(client))
            for _ in range(2):
                answer = await agent.run('Find the Example returns policy')
                assert '14 days' in answer
                assert 'https://example.com/returns' in answer
        assert requests == ['/v2/search', '/v2/scrape'] * 2

    asyncio.run(exercise())
    assert 'Search started' in caplog.text
    assert 'Page read started' in caplog.text


@pytest.mark.parametrize('max_requests,max_tools', [(1, 10), (8, 0)])
def test_research_stops_before_exceeding_execution_budget(max_requests: int, max_tools: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, "MAX_MODEL_REQUESTS", max_requests)
    monkeypatch.setattr(constants, "MAX_TOOL_CALLS", max_tools)
    calls: list[str] = []

    def web(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(200, json={'success': True, 'data': {'web': []}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart('search', {'queries': ['public topic']})])

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            agent = ResearchAgent(FunctionModel(model), Firecrawl(client))
            with pytest.raises(UsageLimitExceeded):
                await agent.run('Research a public topic')
        assert len(calls) == (0 if max_tools == 0 else 1)

    asyncio.run(exercise())


def test_research_timeout_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, "RUN_TIMEOUT_SECONDS", 0.01)
    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(1)
        return ModelResponse(parts=[TextPart('too late')])

    async def exercise() -> None:
        async with httpx2.AsyncClient() as client:
            with pytest.raises(TimeoutError):
                await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('Topic')

    asyncio.run(exercise())




@pytest.mark.parametrize('failure', ['http', 'malformed', 'oversized', 'private_url', 'blocked_page', 'malformed_url'])
def test_web_failures_are_returned_to_the_model_without_raw_errors(failure: str) -> None:
    requests: list[str] = []

    def web(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        if failure == 'http':
            return httpx2.Response(500, text='secret-server-error')
        if failure == 'malformed':
            return httpx2.Response(200, json={'success': True, 'data': {'markdown': None}})
        if failure == 'blocked_page':
            return httpx2.Response(200, json={'success': True, 'data': {
                'markdown': 'Access denied', 'metadata': {'statusCode': 403}}})
        return httpx2.Response(200, content=b'x' * 2_000_001)

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not results:
            url = 'http://127.0.0.1/secrets' if failure == 'private_url' else 'https://example.com/policy'
            if failure == 'malformed_url':
                url = 'https://[invalid]/'
            return ModelResponse(parts=[ToolCallPart('read_page', {'url': url})])
        assert 'error' in str(results[-1].content)
        assert 'secret-server-error' not in str(results[-1].content)
        return ModelResponse(parts=[TextPart('I could not verify this page.')])

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            answer = await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('Find the policy')
            assert answer == 'I could not verify this page.'
        if failure in {'private_url', 'malformed_url'}:
            assert requests == []

    asyncio.run(exercise())


def test_agent_can_read_later_sections_of_a_long_page() -> None:
    def web(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={'success': True, 'data': {
            'markdown': 'Intro ' * 3000 + 'Deadline cancellation is described here.'}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not results:
            return ModelResponse(parts=[ToolCallPart('read_page', {'url': 'https://example.com/long'})])
        if len(results) == 1:
            assert isinstance(results[-1].content, dict)
            assert results[-1].content.get('next_offset') == 16000
            return ModelResponse(parts=[ToolCallPart('read_page', {'url': 'https://example.com/long', 'offset': 16000})])
        assert 'Deadline cancellation' in str(results[-1].content)
        return ModelResponse(parts=[TextPart('Found the deadline section.')])

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            assert await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('Find deadlines') == 'Found the deadline section.'

    asyncio.run(exercise())


def test_rejected_credential_url_is_not_printed(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger='research')
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
            return ModelResponse(parts=[TextPart('Please use a public URL without credentials.')])
        return ModelResponse(parts=[ToolCallPart('read_page', {'url': 'https://user:secret-password@example.com/'})])

    async def exercise() -> None:
        async with httpx2.AsyncClient() as client:
            await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('Read a page')

    asyncio.run(exercise())
    assert 'secret-password' not in caplog.text


def test_planned_queries_are_searched_once_and_sources_are_deduplicated() -> None:
    queries: list[str] = []

    def web(request: httpx2.Request) -> httpx2.Response:
        query = json.loads(request.content)['query']
        queries.append(query)
        if query == 'support procedure':
            return httpx2.Response(503)
        return httpx2.Response(200, json={'success': True, 'data': {'web': [
            {'url': 'https://example.com/policy', 'title': 'Policy'},
            {'url': f'https://example.com/{len(queries)}', 'title': 'Details'},
        ]}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not results:
            return ModelResponse(parts=[ToolCallPart('search', {
                'queries': ['official policy', 'eligibility exceptions', 'support procedure'],
            })])
        content = results[-1].content
        assert isinstance(content, dict)
        sources = content.get('sources')
        assert isinstance(sources, list) and len(sources) == 3
        assert content.get('failed_queries') == 1
        return ModelResponse(parts=[TextPart('Policy evidence found; support search was unavailable.')])

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            answer = await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('Find policy and support options')
            assert 'support search was unavailable' in answer
        assert queries == ['official policy', 'eligibility exceptions', 'support procedure']

    asyncio.run(exercise())


def test_query_batch_limit_prevents_excess_web_requests() -> None:
    requests: list[str] = []

    def web(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        return httpx2.Response(200, json={'success': True, 'data': {'web': []}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart('search', {'queries': ['a', 'b', 'c', 'd']})])

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            with pytest.raises(UnexpectedModelBehavior):
                await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('Search')
        assert requests == []

    asyncio.run(exercise())






@pytest.mark.parametrize('data, expected_failures', [({'web': []}, 0), ({}, 1)])
def test_empty_search_distinguishes_explicit_results_from_ambiguous_response(
    data: dict[str, Any], expected_failures: int,
) -> None:
    def web(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={'success': True, 'data': data})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not results:
            return ModelResponse(parts=[ToolCallPart('search', {'queries': ['narrow public topic']})])
        content = results[-1].content
        assert isinstance(content, dict)
        assert content.get('sources') == []
        assert content.get('failed_queries') == expected_failures
        assert ('error' in content) is bool(expected_failures)
        return ModelResponse(parts=[TextPart('No matching sources were found.')])

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            assert await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('Research') == 'No matching sources were found.'

    asyncio.run(exercise())

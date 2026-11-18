import asyncio
import json
from typing import Any

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from personal_agent.firecrawl import Firecrawl
from personal_agent.research import ResearchAgent
from personal_agent.telemetry import LangfuseTrace


def test_research_searches_reads_and_answers_with_fresh_history(capsys: pytest.CaptureFixture[str]) -> None:
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
            return ModelResponse(parts=[ToolCallPart('search', {'query': 'Example returns policy'})])
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
    transcript = capsys.readouterr().out
    assert '[tool] search' in transcript
    assert '[tool] read_page' in transcript
    assert '[model] request 3' in transcript


@pytest.mark.parametrize('max_requests,max_tools', [(1, 10), (8, 0)])
def test_research_stops_before_exceeding_execution_budget(max_requests: int, max_tools: int) -> None:
    calls: list[str] = []

    def web(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(200, json={'success': True, 'data': {'web': []}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart('search', {'query': 'public topic'})])

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            agent = ResearchAgent(FunctionModel(model), Firecrawl(client),
                                  max_requests=max_requests, max_tool_calls=max_tools)
            with pytest.raises(RuntimeError, match='^execution_limit$'):
                await agent.run('Research a public topic')
        assert len(calls) == (0 if max_tools == 0 else 1)

    asyncio.run(exercise())


def test_research_timeout_is_sanitized() -> None:
    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(1)
        return ModelResponse(parts=[TextPart('too late')])

    async def exercise() -> None:
        async with httpx2.AsyncClient() as client:
            with pytest.raises(RuntimeError, match='^timeout$'):
                await ResearchAgent(FunctionModel(model), Firecrawl(client), timeout=0.01).run('Topic')

    asyncio.run(exercise())


@pytest.mark.parametrize('trace_status', [200, 503])
def test_research_exports_only_metadata_and_survives_trace_failure(trace_status: int) -> None:
    exported: list[dict[str, Any]] = []

    def trace(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == '/api/public/otel/v1/traces'
        body = json.loads(request.content)
        exported.append(body)
        serialized = request.content.decode()
        for secret in ['private-prompt', 'private-answer', 'private-query', 'private-page', 'secret-token']:
            assert secret not in serialized
        return httpx2.Response(trace_status, json={} if trace_status == 200 else {'error': 'private-server-error'})

    def web(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={'success': True, 'data': {'web': [
            {'url': 'https://example.com', 'description': 'private-page'}]}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
            return ModelResponse(parts=[TextPart('private-answer')])
        return ModelResponse(parts=[ToolCallPart('search', {'query': 'private-query'})])

    async def exercise() -> None:
        async with (httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as web_client,
                    httpx2.AsyncClient(base_url='http://langfuse', auth=('public-key', 'secret-token'),
                                      transport=httpx2.MockTransport(trace)) as trace_client):
            agent = ResearchAgent(FunctionModel(model), Firecrawl(web_client), tracing=LangfuseTrace(trace_client))
            assert await agent.run('private-prompt') == 'private-answer'
        span = exported[0]['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
        attrs = {a['key']: a['value'] for a in span['attributes']}
        assert attrs['agent.tool_names']['stringValue'] == 'search'
        assert attrs['agent.requests']['intValue'] == '2'
        assert span['name'] == 'research'
        assert int(span['endTimeUnixNano']) >= int(span['startTimeUnixNano'])

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


def test_rejected_credential_url_is_not_printed(capsys: pytest.CaptureFixture[str]) -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
            return ModelResponse(parts=[TextPart('Please use a public URL without credentials.')])
        return ModelResponse(parts=[ToolCallPart('read_page', {'url': 'https://user:secret-password@example.com/'})])

    async def exercise() -> None:
        async with httpx2.AsyncClient() as client:
            await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('Read a page')

    asyncio.run(exercise())
    assert 'secret-password' not in capsys.readouterr().out

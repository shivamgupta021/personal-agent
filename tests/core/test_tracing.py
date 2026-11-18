import asyncio
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
from django.conf import settings
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from requests import PreparedRequest, Response, Session

from core.tracing import tracing
from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl


class CaptureSession(Session):
    """HTTP boundary: capture the SDK's actual serialized OTLP request."""
    def __init__(self, status_code: int = 200) -> None:
        super().__init__()
        self.bodies: list[bytes] = []
        self.status_code = status_code

    def send(self, request: PreparedRequest, **kwargs: Any) -> Response:
        assert isinstance(request.body, bytes)
        self.bodies.append(request.body)
        response = Response()
        response.status_code = self.status_code
        response._content = b''
        return response

    def spans(self) -> list[Span]:
        return [span for body in self.bodies
                for resource in ExportTraceServiceRequest.FromString(body).resource_spans
                for scope in resource.scope_spans for span in scope.spans]


@pytest.fixture
def native_tracing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[CaptureSession]:
    session = CaptureSession()
    monkeypatch.setattr(settings, "LANGFUSE_CAPTURE_CONTENT", getattr(request, "param", False))
    tracing.configure(OTLPSpanExporter(endpoint='http://langfuse/api/public/otel/v1/traces', session=session))
    try:
        yield session
    finally:
        tracing.shutdown()


@pytest.mark.parametrize("native_tracing", [False, True], indirect=True)
def test_native_spans_capture_tools_and_usage_with_configured_content(native_tracing: CaptureSession, request: pytest.FixtureRequest) -> None:
    def web(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={'success': True, 'data': {'web': [
            {'url': 'https://example.com/private-url', 'description': 'private-page'}]}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
            return ModelResponse(parts=[TextPart('private-answer')])
        return ModelResponse(parts=[ToolCallPart('search', {'queries': ['private-query']})])

    async def exercise() -> None:
        async with httpx2.AsyncClient(base_url='http://firecrawl', transport=httpx2.MockTransport(web)) as client:
            assert await ResearchAgent(FunctionModel(model, model_name='controlled-model'), Firecrawl(client)).run(
                'private-prompt') == 'private-answer'

    asyncio.run(exercise())
    tracing.flush()
    bodies = b''.join(native_tracing.bodies)
    for secret in [b'private-prompt', b'private-answer', b'private-query', b'private-page', b'private-url']:
        assert (secret in bodies) is request.node.callspec.params['native_tracing']
    spans = native_tracing.spans()
    assert len(spans) >= 4  # agent run, two model calls, one tool call
    assert any('search' in span.name for span in spans)
    assert any(a.key == 'gen_ai.usage.input_tokens' for span in spans for a in span.attributes)
    assert len({span.trace_id for span in spans}) == 1
    assert any(span.parent_span_id for span in spans)


def test_native_error_spans_do_not_export_exception_message(native_tracing: CaptureSession) -> None:
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ValueError('private-provider-error')

    async def exercise() -> None:
        async with httpx2.AsyncClient() as client:
            with pytest.raises(ValueError, match='private-provider-error'):
                await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('private-failed-prompt')

    asyncio.run(exercise())
    tracing.flush()
    body = b''.join(native_tracing.bodies)
    assert b'private-provider-error' not in body
    assert b'private-failed-prompt' not in body
    assert any(span.status.code == 2 for span in native_tracing.spans())


def test_export_failure_does_not_discard_answer(native_tracing: CaptureSession) -> None:
    native_tracing.status_code = 400

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart('Still usable')])

    async def exercise() -> None:
        async with httpx2.AsyncClient() as client:
            assert await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('A question') == 'Still usable'

    asyncio.run(exercise())
    tracing.flush()
    assert native_tracing.bodies


def test_native_traces_isolate_concurrent_runs(native_tracing: CaptureSession) -> None:
    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(0)
        return ModelResponse(parts=[TextPart('Answer')])

    async def exercise() -> None:
        async with httpx2.AsyncClient() as client:
            agent = ResearchAgent(FunctionModel(model), Firecrawl(client))
            answers = await asyncio.gather(agent.run('First'), agent.run('Second'))
            assert tuple(answers) == ('Answer', 'Answer')

    asyncio.run(exercise())
    tracing.flush()
    spans = native_tracing.spans()
    assert len({span.trace_id for span in spans}) == 2
    assert len([span for span in spans if not span.parent_span_id]) == 2


def test_native_tracing_preserves_cancellation_without_prompt_content(native_tracing: CaptureSession) -> None:
    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise asyncio.CancelledError()

    async def exercise() -> None:
        async with httpx2.AsyncClient() as client:
            with pytest.raises(asyncio.CancelledError):
                await ResearchAgent(FunctionModel(model), Firecrawl(client)).run('private-cancelled-prompt')

    asyncio.run(exercise())
    tracing.flush()
    assert b'private-cancelled-prompt' not in b''.join(native_tracing.bodies)
    assert native_tracing.spans()
    assert all(span.end_time_unix_nano >= span.start_time_unix_nano for span in native_tracing.spans())

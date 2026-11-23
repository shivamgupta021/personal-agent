"""Captured application content remains associated until its final producer closes."""

import asyncio
import json
from contextlib import nullcontext
from collections.abc import Sequence

import httpx2
import pytest
from django.test import override_settings
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.tracing import tracing
from core.turns import OwnerTurn
from grievances.cleanup import CaseTraceCleanup
from grievances.models import CaseTrace
from grievances.service import ApplicationService


class RemoteTraceExporter(InMemorySpanExporter):
    """Model ingestion and deletion, including late export recreating a trace."""
    def __init__(self) -> None:
        super().__init__()
        self.remote: dict[str, list[str]] = {}
        self.deleted: set[str] = set()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            assert span.context is not None
            self.remote.setdefault(format(span.context.trace_id, '032x'), []).append(span.to_json())
        return super().export(spans)

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == 'DELETE':
            trace_id = request.url.path.rsplit('/', 1)[1]
            self.remote.pop(trace_id, None)
            self.deleted.add(trace_id)
            return httpx2.Response(204)
        trace_id = request.url.params['traceId']
        return httpx2.Response(200, json={'data': [{'traceId': trace_id}] if trace_id in self.remote else [],
                                       'meta': {'cursor': None}})


def capture(exporter: RemoteTraceExporter) -> None:
    tracing.shutdown()
    tracing.configure(exporter)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('ambient', [False, True])
def test_application_forgetting_does_not_export_captured_request_after_cleanup(ambient: bool) -> None:
    exporter = RemoteTraceExporter()

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'action': 'memory'})])
        return ModelResponse(parts=[ToolCallPart('forget_conversation', {})])

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://trace.invalid', transport=httpx2.MockTransport(exporter.respond)) as client:
            app = ApplicationService(FunctionModel(respond), trace_cleanup=CaseTraceCleanup(client))
            outer_context = tracing.operation('experiment', {'fixture': 'fictional'}) if ambient else nullcontext()
            with outer_context as outer:
                answer = await app.run(OwnerTurn('trace-owner', 'chat', 'forget', 'Forget this conversation about SYNTHETIC-RUBY.'))
                assert 'deleted locally' in answer
                assert exporter.deleted
                assert not exporter.remote
            tracing.flush()
            assert 'SYNTHETIC-RUBY' not in json.dumps(exporter.remote)
            assert exporter.deleted.isdisjoint(exporter.remote)
            spans = exporter.get_finished_spans()
            root = next(span for span in spans if span.name == 'application.turn')
            assert root.parent is None
            if ambient:
                assert outer is not None and root.context is not None
                parent_context = outer.get_span_context()
                assert root.context.trace_id != parent_context.trace_id
                assert root.links[0].context == parent_context
                assert root.attributes is not None
                assert root.attributes['langfuse.observation.metadata.ambient_trace_id'] == format(parent_context.trace_id, '032x')
                assert set(exporter.remote) == {format(parent_context.trace_id, '032x')}
            else:
                assert not exporter.remote
            assert any('SYNTHETIC-RUBY' in span.to_json() for span in spans)
            assert any(span.name == 'application.turn' for span in spans)

    try:
        with override_settings(LANGFUSE_CAPTURE_CONTENT=True, LANGFUSE_CAPTURE_PRIVATE_CONTENT=True):
            capture(exporter)
            asyncio.run(run())
    finally:
        tracing.shutdown()


@pytest.mark.django_db(transaction=True)
def test_case_capture_registers_directory_and_new_cases_for_deletion() -> None:
    exporter = RemoteTraceExporter()

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = json.loads(str(messages[-1].parts[0].content))['current_request']  # type: ignore[union-attr]
        if request.startswith('Create'):
            label = 'Alpha' if 'Alpha' in request else 'Beta'
            action = {'action': 'create', 'label': label, 'intent_quote': 'Create a case'}
        else:
            action = {'action': 'clarify'}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, action)])

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://trace.invalid', transport=httpx2.MockTransport(exporter.respond)) as client:
            app = ApplicationService(FunctionModel(respond), trace_cleanup=CaseTraceCleanup(client))
            for delivery, message in [('a', 'Create a case Alpha'), ('b', 'Create a case Beta'), ('c', 'Which case?')]:
                await app.run(OwnerTurn('trace-owner', 'chat', delivery, message))
            cases = await app.store.list_cases('trace-owner')
            alpha = next(case for case in cases if case.label == 'Alpha')
            beta = next(case for case in cases if case.label == 'Beta')
            alpha_traces = {row.trace_id async for row in CaseTrace.objects.filter(case_id=alpha.id)}
            beta_traces = {row.trace_id async for row in CaseTrace.objects.filter(case_id=beta.id)}
            assert alpha_traces & beta_traces
            assert len(alpha_traces) == 3
            assert len(beta_traces) == 2
            await app.run(OwnerTurn('trace-owner', 'chat', 'delete-a', 'Delete case Alpha'))
            assert alpha_traces <= exporter.deleted
            assert not (alpha_traces & exporter.remote.keys())
            await app.run(OwnerTurn('trace-owner', 'chat', 'delete-b', 'Delete case Beta'))
            assert beta_traces <= exporter.deleted
            assert not await CaseTrace.objects.filter(pending=True).aexists()
            assert not exporter.remote
            replay = await app.run(OwnerTurn('trace-owner', 'chat', 'a', 'Create a case Alpha'))
            assert 'nothing was recreated' in replay
            assert 'Alpha' not in json.dumps(exporter.remote)

    try:
        with override_settings(LANGFUSE_CAPTURE_CONTENT=True, LANGFUSE_CAPTURE_PRIVATE_CONTENT=True):
            capture(exporter)
            asyncio.run(run())
    finally:
        tracing.shutdown()

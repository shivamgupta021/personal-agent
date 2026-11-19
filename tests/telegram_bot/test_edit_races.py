import asyncio
import json
from collections.abc import Sequence
from typing import Any

import httpx2
import pytest
from django.test import override_settings
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from conversations.budget import ContextBudgetExceeded
from conversations.cleanup import TraceCleanup
from conversations.service import ConversationService
from core.tracing import tracing
from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl


class RemoteTraceStore(SpanExporter):
    def __init__(self) -> None:
        self.traces: dict[str, list[str]] = {}
        self.deletions: list[str] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            if span.context:
                trace_id = format(span.context.trace_id, '032x')
                self.traces.setdefault(trace_id, []).append(str(span.attributes))
        return SpanExportResult.SUCCESS

    def delete(self, request: httpx2.Request) -> httpx2.Response:
        trace_id = request.url.path.rsplit('/', 1)[-1]
        self.deletions.append(trace_id)
        self.traces.pop(trace_id, None)
        return httpx2.Response(202)


def edit_response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    if 'FACT_EDIT_AUTHORIZATION' in str(info):
        value: dict[str, Any] = {'action': 'forget', 'scope': 'parcel code', 'replacement': '', 'question': ''}
    elif 'FACT_EDIT_REDACTION' in str(info):
        data = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
        value = {'edits': [{'segment_id': s['id'], 'quote': 'My parcel code is SECRET-1.'}
                          for s in data['segments'] if 'My parcel code is SECRET-1.' in s['text']]}
    elif 'FACT_EDIT_VERIFICATION' in str(info):
        value = {'safe': True}
    else:
        if any(isinstance(p, ToolReturnPart) for p in messages[-1].parts):
            return ModelResponse(parts=[TextPart('No edit committed.')])
        return ModelResponse(parts=[ToolCallPart('edit_conversation_fact', {'target': 'parcel code'})])
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])


@pytest.mark.django_db(transaction=True)
@override_settings(LANGFUSE_CAPTURE_CONTENT=True)
@pytest.mark.parametrize('failure', ['provider', 'cancel', 'budget'])
def test_failed_stale_turn_cannot_recreate_deleted_trace(failure: str) -> None:
    remote = RemoteTraceStore()
    tracing.configure(remote)

    async def run() -> None:
        started, resume = asyncio.Event(), asyncio.Event()

        async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            started.set()
            await resume.wait()
            if failure == 'cancel':
                raise asyncio.CancelledError
            if failure == 'budget':
                raise ContextBudgetExceeded
            raise RuntimeError('controlled provider failure')

        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web,
            httpx2.AsyncClient(base_url='https://langfuse.invalid/', transport=httpx2.MockTransport(remote.delete)) as lf,
        ):
            cleanup = TraceCleanup(lf).run
            await ConversationService('stale-failure', ResearchAgent(FunctionModel(
                lambda m, i: ModelResponse(parts=[TextPart('Acknowledged.')])), Firecrawl(web)), cleanup).run('My parcel code is SECRET-1.')
            stale = asyncio.create_task(ConversationService('stale-failure', ResearchAgent(FunctionModel(slow), Firecrawl(web)), cleanup).run('Repeat my code.'))
            await started.wait()
            reply = await ConversationService('stale-failure', ResearchAgent(FunctionModel(edit_response), Firecrawl(web)), cleanup).run('Forget my parcel code.')
            assert 'forgotten locally' in reply
            first_deletions = len(remote.deletions)
            resume.set()
            await asyncio.gather(stale, return_exceptions=True)
            assert len(remote.deletions) > first_deletions
            tracing.flush()
            assert not remote.traces
    try:
        asyncio.run(run())
    finally:
        tracing.shutdown()


@pytest.mark.django_db(transaction=True)
def test_edit_does_not_overwrite_a_newly_completed_unrelated_turn() -> None:
    async def run() -> None:
        planning, resume = asyncio.Event(), asyncio.Event()

        async def editor(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if 'FACT_EDIT_REDACTION' in str(info):
                planning.set()
                await resume.wait()
            return edit_response(messages, info)

        def inspect(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            assert 'Rust' in str(messages)
            assert 'SECRET-1' in str(messages)  # The stale edit was rejected, not partially applied.
            return ModelResponse(parts=[TextPart('Rust is retained.')])

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web:
            normal = ResearchAgent(FunctionModel(lambda m, i: ModelResponse(parts=[TextPart('Acknowledged.')])), Firecrawl(web))
            await ConversationService('race-edit', normal).run('My parcel code is SECRET-1.')
            editing = asyncio.create_task(ConversationService('race-edit', ResearchAgent(FunctionModel(editor), Firecrawl(web))).run('Forget my parcel code.'))
            await planning.wait()
            await ConversationService('race-edit', normal).run('My comparison language is Rust.')
            resume.set()
            assert 'state changed' in await editing
            assert await ConversationService('race-edit', ResearchAgent(FunctionModel(inspect), Firecrawl(web))).run('What is my language?') == 'Rust is retained.'
    asyncio.run(run())

import asyncio
import json
from pathlib import Path
from collections.abc import Iterator

from asgiref.sync import sync_to_async
from django.db import connections
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from conversations.service import ConversationService
from conversations import constants
from conversations.cleanup import TraceCleanup
from core.tracing import tracing
from telegram_bot.client import TelegramError
from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl
from telegram_bot.client import TelegramClient
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState


@pytest.mark.django_db(transaction=True)
def test_follow_up_retains_context_after_restart(tmp_path: Path) -> None:
    replies: list[str] = []
    turn = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            turn += 1
            text = 'My comparison is Python versus Ruby.' if turn == 1 else 'Which languages did I mention?'
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': turn, 'message': {'from': {'id': 7},
                'chat': {'id': 7, 'type': 'private'}, 'text': text},
            }]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web:
                model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart(
                    'Python and Ruby' if 'Python versus Ruby' in str(messages) else 'I do not know',
                )]))
                for _ in range(2):
                    conversation = ConversationService('test-owner', ResearchAgent(model, Firecrawl(web)))
                    service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                    with service.state.locked():
                        await service.poll_once()
    asyncio.run(run())
    assert replies == ['Python and Ruby', 'Python and Ruby']


@pytest.mark.django_db(transaction=True)
def test_forgetting_stops_old_context_and_requests_trace_cleanup(tmp_path: Path) -> None:
    from_context: list[str] = []
    replies: list[str] = []
    turn = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            texts = ['My example code is violet.', 'Please erase our whole chat history.', 'What was my example code?']
            text = texts[turn]
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': turn, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text},
            }]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        from_context.append(str(messages))
        if 'Please erase our whole chat history.' in str(messages):
            return ModelResponse(parts=[ToolCallPart('forget_conversation', {})])
        return ModelResponse(parts=[TextPart('violet' if 'violet' in str(messages) else 'I do not know')])

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web:
                for _ in range(3):
                    conversation = ConversationService('test-forget', ResearchAgent(FunctionModel(respond), Firecrawl(web)))
                    service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                    with service.state.locked():
                        await service.poll_once()
    asyncio.run(run())
    assert replies[0] == 'violet'
    assert 'deleted locally' in replies[1]
    assert replies[2] == 'I do not know'
    assert 'violet' not in from_context[-1]


@pytest.mark.django_db(transaction=True)
def test_oversized_follow_up_keeps_previous_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[str] = []
    turn = 0
    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            texts = ['My example is amber.', 'x' * 50_000, 'What was my example?', 'Delete this entire conversation.', 'What was my example?']
            text = texts[turn]
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': turn, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text},
            }]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})
    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web:
                model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart(
                    'amber' if 'amber' in str(messages) else 'unknown',
                )]))
                conversation = ConversationService('test-budget', ResearchAgent(model, Firecrawl(web)))
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                with service.state.locked():
                    for index in range(5):
                        # Prove deletion works even when the retained history itself cannot fit.
                        monkeypatch.setattr(constants, 'MAX_REQUEST_BYTES', 1 if index == 3 else 48_000)
                        await service.poll_once()
    asyncio.run(run())
    assert replies[0] == replies[2] == 'amber'
    assert 'saved history is unchanged' in replies[1]
    assert 'deleted locally' in replies[3]
    assert replies[4] == 'unknown'


@pytest.fixture(autouse=True)
def close_async_connections() -> Iterator[None]:
    yield
    asyncio.run(sync_to_async(connections.close_all)())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('interrupt', [False, True])
def test_failed_delivery_keeps_answer_but_interrupted_model_does_not(tmp_path: Path, interrupt: bool) -> None:
    replies: list[str] = []
    turn = 0
    model_calls = 0
    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            turn += 1
            text = 'My example is coral.' if turn == 1 else 'What example did I give?'
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': turn, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text},
            }]})
        if turn == 1:
            raise httpx2.ReadTimeout('uncertain delivery', request=request)
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})
    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if interrupt and model_calls == 1:
            raise asyncio.CancelledError
        return ModelResponse(parts=[TextPart('coral' if 'coral' in str(messages) else 'unknown')])
    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web:
                for _ in range(2):
                    conversation = ConversationService('test-interruption', ResearchAgent(FunctionModel(model), Firecrawl(web)))
                    service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                    with service.state.locked():
                        try:
                            await service.poll_once()
                        except (TelegramError, asyncio.CancelledError):
                            pass
    asyncio.run(run())
    assert replies == (['unknown'] if interrupt else ['coral'])


@pytest.mark.django_db(transaction=True)
def test_trace_cleanup_failure_is_retryable_without_restoring_history(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    tracing.configure(exporter)
    requested: list[str] = []
    replies: list[str] = []
    turn = 0
    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            texts = ['My code is silver.', 'Delete this entire conversation.', 'retry trace cleanup', 'What was my code?']
            text = texts[turn]
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': turn, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text},
            }]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})
    def langfuse(request: httpx2.Request) -> httpx2.Response:
        assert request.method == 'DELETE'
        assert not request.content
        requested.append(request.url.path.rsplit('/', 1)[1])
        return httpx2.Response(503 if turn == 2 else 202)
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'Delete this entire conversation.' in str(messages):
            return ModelResponse(parts=[ToolCallPart('forget_conversation', {})])
        return ModelResponse(parts=[TextPart('silver' if 'silver' in str(messages) else 'unknown')])
    async def run() -> None:
        async with (
            httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http,
            httpx2.AsyncClient(base_url='https://langfuse.invalid/', transport=httpx2.MockTransport(langfuse)) as lf,
            httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web,
        ):
            for _ in range(4):
                conversation = ConversationService('test-cleanup', ResearchAgent(FunctionModel(model), Firecrawl(web)), TraceCleanup(lf).run)
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                with service.state.locked():
                    await service.poll_once()
    try:
        asyncio.run(run())
        assert 'failed' in replies[1]
        assert 'deletion requested' in replies[2]
        assert replies[3] == 'unknown'
        assert len(set(requested)) == 1
        assert requested[:1] == requested[1:]
    finally:
        tracing.shutdown()


@pytest.mark.django_db(transaction=True)
def test_compaction_preserves_early_fact_across_restart(tmp_path: Path) -> None:
    replies: list[str] = []
    turn = 0
    summaries = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            text = ('My code is indigo. ' if turn == 0 else 'Continue. ') + 'background ' * 1200
            turn += 1
            if turn == 6:
                text = 'What is my code?'
            if turn == 7:
                text = 'Delete this entire conversation.'
            if turn == 8:
                text = 'What is my code?'
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': turn, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text},
            }]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal summaries
        if 'context summarization assistant' in str(info):
            summaries += 1
            return ModelResponse(parts=[TextPart('The user code is indigo. No outstanding research.')])
        return ModelResponse(parts=[TextPart('indigo' if 'indigo' in str(messages) else 'unknown')])

    async def run() -> None:
        async with (
            httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http,
            httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web,
        ):
            for _ in range(8):
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7,
                    ConversationService('compaction-test', ResearchAgent(FunctionModel(model), Firecrawl(web))).run)
                with service.state.locked():
                    await service.poll_once()
    asyncio.run(run())
    assert replies[:6] == ['indigo'] * 6
    assert 'deleted locally' in replies[6]
    assert replies[7] == 'unknown'
    assert summaries >= 2


@pytest.mark.django_db(transaction=True)
def test_summary_failure_keeps_saved_context_and_only_continues_within_budget(tmp_path: Path) -> None:
    replies: list[str] = []
    turn = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            text = ('My code is teal. ' if turn == 0 else 'Continue. ') + 'background ' * 1200
            turn += 1
            if turn == 4:
                text = 'What is my code?'
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': turn, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text},
            }]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'context summarization assistant' in str(info):
            raise TimeoutError('simulated summary outage')
        return ModelResponse(parts=[TextPart('teal' if 'teal' in str(messages) else 'unknown')])

    async def run() -> None:
        async with (
            httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http,
            httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web,
        ):
            for _ in range(4):
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7,
                    ConversationService('failed-compaction', ResearchAgent(FunctionModel(model), Firecrawl(web))).run)
                with service.state.locked():
                    await service.poll_once()
    asyncio.run(run())
    assert replies[0] == replies[1] == replies[3] == 'teal'
    assert 'saved history is unchanged' in replies[2]


@pytest.mark.django_db(transaction=True)
def test_large_tool_result_compacts_between_calls_without_orphaning_tools(tmp_path: Path) -> None:
    replies: list[str] = []
    turn = 0
    summary_calls = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            text = 'My code is gold. Acknowledge. ' + 'background ' * 1500 if turn == 0 else 'Read https://example.com/policy and recall my code.'
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': turn, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text},
            }]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal summary_calls
        if 'context summarization assistant' in str(info):
            summary_calls += 1
            return ModelResponse(parts=[TextPart('The user code is gold. Read the policy; evidence remains unverified.')])
        calls = {p.tool_call_id for m in messages for p in m.parts if isinstance(p, ToolCallPart)}
        returns = {p.tool_call_id for m in messages for p in m.parts if isinstance(p, ToolReturnPart)}
        assert returns <= calls
        if turn == 2 and not returns:
            return ModelResponse(parts=[ToolCallPart('read_page', {'url': 'https://example.com/policy'})])
        return ModelResponse(parts=[TextPart('gold' if 'gold' in str(messages) else 'unknown')])

    async def run() -> None:
        async with (
            httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http,
            httpx2.AsyncClient(base_url='https://firecrawl.invalid', transport=httpx2.MockTransport(
                lambda r: httpx2.Response(200, json={'success': True, 'data': {'markdown': 'Policy detail. ' * 1200}}))) as web,
        ):
            for _ in range(2):
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7,
                    ConversationService('tool-compaction', ResearchAgent(FunctionModel(model), Firecrawl(web))).run)
                with service.state.locked():
                    await service.poll_once()
    asyncio.run(run())
    assert replies == ['gold', 'gold']
    assert summary_calls >= 1

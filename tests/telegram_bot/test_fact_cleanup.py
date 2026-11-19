import asyncio
import json
from pathlib import Path
from asgiref.sync import sync_to_async
from django.db import connection
from typing import Any

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from conversations import constants
from conversations.cleanup import TraceCleanup
from conversations.models import ConversationRecord, ConversationSummary
from core.tracing import tracing
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from conversations.service import ConversationService
from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl
from telegram_bot.client import TelegramClient, TelegramError
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('operation', ['forget', 'correct', 'quoted', 'bad_quote', 'unsafe', 'timeout', 'delivery_failure', 'repeat_forget', 'mixed_request', 'slow_commit', 'rejected_correction', 'authorization_timeout'])
def test_targeted_edit_preserves_unrelated_facts_after_restart(tmp_path: Path, operation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    turns = ['My parcel code is SECRET-1. My favourite colour is teal.',
             ('Correction: my parcel code is UPDATED-2.' if operation in ('correct', 'rejected_correction') else 'Explain this quote: Forget my parcel code.' if operation == 'quoted' else 'Forget my parcel code.'),
             'What are my parcel code and favourite colour?']
    if operation == 'mixed_request':
        turns[1] = 'Forget my parcel code. My comparison language is Rust.'
    if operation == 'slow_commit':
        monkeypatch.setattr(constants, 'FACT_EDIT_TIMEOUT_SECONDS', 1)
    if operation == 'repeat_forget':
        turns.insert(2, 'Forget my parcel code SECRET-1.')
    replies: list[str] = []
    seen: list[str] = []
    turn = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            value = turns[turn]
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{'update_id': turn,
                'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': value}}]})
        replies.append(json.loads(request.content)['text'])
        if operation == 'delivery_failure' and turn == 2:
            raise httpx2.ReadTimeout('simulated delivery loss', request=request)
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def structured(info: AgentInfo, value: dict[str, Any]) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        instructions = str(info)
        if 'FACT_EDIT_AUTHORIZATION' in instructions:
            if operation == 'authorization_timeout':
                raise TimeoutError('controlled authorization failure')
            return structured(info, {'action': 'none' if operation == 'quoted' else 'correct' if operation in ('correct', 'rejected_correction') else 'forget', 'scope': 'parcel code and its value',
                'replacement': 'my parcel code is UPDATED-2.' if operation in ('correct', 'rejected_correction') else '', 'question': ''})
        if 'FACT_EDIT_VERIFICATION' in instructions:
            audit = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            assert audit['latest_user_request'] == turns[turn - 1]
            assert audit['authorized_replacement'] == ('my parcel code is UPDATED-2.' if operation in ('correct', 'rejected_correction') else '')
            return structured(info, {'safe': operation not in ('unsafe', 'rejected_correction')})
        if 'FACT_EDIT_REDACTION' in instructions:
            if operation == 'timeout':
                raise TimeoutError('controlled editor failure')
            data = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            edits = []
            for segment in data['segments']:
                for quote in ['My parcel code is SECRET-1.', 'Your parcel identifier is SECRET-1.', 'Forget my parcel code.', 'Forget my parcel code SECRET-1.', 'Correction: my parcel code is UPDATED-2.']:
                    if quote in segment['text']:
                        edits.append({'segment_id': segment['id'], 'quote': quote if operation != 'bad_quote' else 'fabricated quote'})
            return structured(info, {'edits': edits})
        if turn == 1:
            return ModelResponse(parts=[TextPart('Your parcel identifier is SECRET-1. Your favourite colour is teal.')])
        if turn == 2 or (operation == 'repeat_forget' and turn == 3):
            assert any(t.name == 'edit_conversation_fact' for t in info.function_tools)
            returns = [p for p in messages[-1].parts if isinstance(p, ToolReturnPart)]
            if returns:
                return ModelResponse(parts=[TextPart('Nothing changed: ' + str(returns[-1].content))])
            return ModelResponse(parts=[ToolCallPart('edit_conversation_fact', {'target': 'parcel code'})])
        seen.append(str(messages))
        answer = ('UPDATED-2' if 'UPDATED-2' in str(messages) else 'SECRET-1' if 'SECRET-1' in str(messages) else 'unknown') + ', teal' + (', Rust' if 'Rust' in str(messages) else '')
        return ModelResponse(parts=[TextPart(answer)])

    @sync_to_async
    def delayed_commit(enable: bool) -> None:
        with connection.cursor() as cursor:
            if enable:
                cursor.execute("CREATE FUNCTION test_delay_fact_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(1.5); RETURN NEW; END $$")
                cursor.execute("CREATE TRIGGER test_delay_fact_commit BEFORE UPDATE ON conversations_conversationrecord FOR EACH ROW EXECUTE FUNCTION test_delay_fact_commit()")
            else:
                cursor.execute("DROP FUNCTION IF EXISTS test_delay_fact_commit() CASCADE")

    async def run() -> None:
        async with (
            httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http,
            httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web,
        ):
            for index in range(len(turns)):
                if operation == 'slow_commit' and index == 1:
                    await delayed_commit(True)
                conversation = ConversationService('targeted-owner', ResearchAgent(FunctionModel(model), Firecrawl(web)))
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                with service.state.locked():
                    try:
                        await service.poll_once()
                    except TelegramError:
                        if operation != 'delivery_failure':
                            raise
                    finally:
                        if operation == 'slow_commit' and index == 1:
                            await delayed_commit(False)
    asyncio.run(run())
    if operation in ('quoted', 'bad_quote', 'unsafe', 'timeout', 'rejected_correction', 'authorization_timeout'):
        assert 'Nothing changed' in replies[1]
        assert replies[-1] == 'SECRET-1, teal'
    else:
        assert 'updated locally' in replies[1] if operation in ('correct', 'rejected_correction') else 'forgotten locally' in replies[1]
        assert replies[-1] == ('UPDATED-2, teal' if operation in ('correct', 'rejected_correction') else 'unknown, teal, Rust' if operation == 'mixed_request' else 'unknown, teal')
        assert 'SECRET-1' not in seen[-1]
    if operation in ('bad_quote', 'unsafe', 'timeout', 'rejected_correction', 'authorization_timeout'):
        record = ConversationRecord.objects.get(identity='targeted-owner')
        assert turns[1] not in json.dumps(record.archive) + json.dumps(record.history)
        assert 'UPDATED-2' not in json.dumps(record.archive) + json.dumps(record.history)
    assert 'teal' in seen[-1]
    if operation == 'repeat_forget':
        assert 'already absent' in replies[2]


@pytest.mark.django_db(transaction=True)
def test_targeted_forgetting_purges_compaction_and_recall_and_retries_trace_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, 'COMPACTION_TRIGGER_BYTES', 14_000)
    monkeypatch.setattr(constants, 'RECENT_CONTEXT_BYTES', 500)
    tracing.configure(InMemorySpanExporter())
    turns = ['My parcel code is SECRET-1. My favourite colour is teal.',
             'Continue. ' + 'Background. ' * 650,
             'Continue. ' + 'Background. ' * 650,
             'Recall my parcel code.', 'Forget my parcel code.', 'retry trace cleanup',
             'Recall the parcel code and favourite colour.']
    replies: list[str] = []
    deleted: list[tuple[int, str]] = []
    turn = 0
    summaries = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            value = turns[turn]
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{'update_id': turn,
                'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': value}}]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def langfuse(request: httpx2.Request) -> httpx2.Response:
        assert request.method == 'DELETE' and not request.content
        deleted.append((turn, request.url.path.rsplit('/', 1)[1]))
        return httpx2.Response(503 if turn == 5 else 202)

    def structured(info: AgentInfo, data: dict[str, Any]) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, data)])

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal summaries
        instructions = str(info)
        if 'FACT_EDIT_AUTHORIZATION' in instructions:
            return structured(info, {'action': 'forget', 'scope': 'parcel code and its value', 'replacement': '', 'question': ''})
        if 'FACT_EDIT_REDACTION' in instructions:
            data = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            edits = []
            for segment in data['segments']:
                for quote in ['My parcel code is SECRET-1.', 'Your parcel identifier is SECRET-1.', 'Forget my parcel code.', 'Forget my parcel code SECRET-1.', 'Correction: my parcel code is UPDATED-2.']:
                    if quote in segment['text']:
                        edits.append({'segment_id': segment['id'], 'quote': quote})
            return structured(info, {'edits': edits})
        if 'FACT_EDIT_VERIFICATION' in instructions:
            return structured(info, {'safe': True})
        if 'context summarization assistant' in instructions:
            summaries += 1
            return ModelResponse(parts=[TextPart(('Parcel code SECRET-1. ' if 'SECRET-1' in str(messages) else '') + 'Favourite colour teal.')])
        if turn == 5:
            return ModelResponse(parts=[ToolCallPart('edit_conversation_fact', {'target': 'parcel code'})])
        if turn in (4, 7):
            returned = [p for p in messages[-1].parts if isinstance(p, ToolReturnPart)]
            if not returned:
                return ModelResponse(parts=[ToolCallPart('recall_conversation', {'query': 'parcel' if turn == 4 else 'colour'})])
            if turn == 4:
                return ModelResponse(parts=[TextPart('Your parcel identifier is SECRET-1.')])
            assert 'SECRET-1' not in str(messages)
            assert 'teal' in str(returned)
            return ModelResponse(parts=[TextPart('Parcel unknown; favourite colour teal.')])
        return ModelResponse(parts=[TextPart('Acknowledged.')])

    async def run() -> None:
        async with (
            httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http,
            httpx2.AsyncClient(base_url='https://langfuse.invalid/', transport=httpx2.MockTransport(langfuse)) as lf,
            httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web,
        ):
            for _ in turns:
                conversation = ConversationService('compacted-cleanup', ResearchAgent(FunctionModel(model), Firecrawl(web)), TraceCleanup(lf).run)
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                with service.state.locked():
                    await service.poll_once()
                if turn == 5:
                    assert not await sync_to_async(ConversationSummary.objects.filter(conversation__identity='compacted-cleanup').exists)()
    try:
        asyncio.run(run())
        assert summaries >= 2
        assert 'forgotten locally' in replies[4] and 'failed' in replies[4]
        assert 'deletion requested' in replies[5]
        assert replies[6] == 'Parcel unknown; favourite colour teal.'
        assert {identifier for when, identifier in deleted if when == 5} == {identifier for when, identifier in deleted if when == 6}
        assert len({identifier for when, identifier in deleted}) == 5
        record = ConversationRecord.objects.get(identity='compacted-cleanup')
        assert 'SECRET-1' not in json.dumps(record.archive) + json.dumps(record.history)
        assert all('SECRET-1' not in json.dumps(summary.snapshot) for summary in ConversationSummary.objects.filter(conversation=record))
    finally:
        tracing.shutdown()


@pytest.mark.django_db(transaction=True)
def test_in_flight_turn_cannot_restore_forgotten_context() -> None:
    async def run() -> None:
        started = asyncio.Event()
        resume = asyncio.Event()

        async def slow_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            started.set()
            await resume.wait()
            return ModelResponse(parts=[TextPart('Stale answer SECRET-1')])

        def editor(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if 'FACT_EDIT_AUTHORIZATION' in str(info):
                value: dict[str, Any] = {'action': 'forget', 'scope': 'parcel code', 'replacement': '', 'question': ''}
            elif 'FACT_EDIT_REDACTION' in str(info):
                data = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
                value = {'edits': [{'segment_id': s['id'], 'quote': quote}
                                   for s in data['segments'] for quote in ['My parcel code is SECRET-1.', 'Forget my parcel code.'] if quote in s['text']]}
            elif 'FACT_EDIT_VERIFICATION' in str(info):
                value = {'safe': True}
            else:
                return ModelResponse(parts=[ToolCallPart('edit_conversation_fact', {'target': 'parcel code'})])
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])

        def inspect(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            assert 'SECRET-1' not in str(messages)
            assert 'teal' in str(messages)
            return ModelResponse(parts=[TextPart('Unknown parcel; teal.')])

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web:
            await ConversationService('concurrent-owner', ResearchAgent(FunctionModel(
                lambda messages, info: ModelResponse(parts=[TextPart('Acknowledged.')])), Firecrawl(web))).run(
                    'My parcel code is SECRET-1. My favourite colour is teal.')
            stale = asyncio.create_task(ConversationService('concurrent-owner', ResearchAgent(FunctionModel(slow_model), Firecrawl(web))).run('Repeat my code.'))
            await started.wait()
            result = await ConversationService('concurrent-owner', ResearchAgent(FunctionModel(editor), Firecrawl(web))).run('Forget my parcel code.')
            assert 'forgotten locally' in result
            resume.set()
            assert 'state changed' in await stale
            assert await ConversationService('concurrent-owner', ResearchAgent(FunctionModel(inspect), Firecrawl(web))).run('What do you remember?') == 'Unknown parcel; teal.'
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_targeted_forgetting_retains_unrelated_tool_evidence(tmp_path: Path) -> None:
    turns = ['Read https://example.com/SECRET-1/policy.', 'Forget the parcel code and page offset.', 'What does the policy say?']
    replies: list[str] = []
    turn = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            value = turns[turn]
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{'update_id': turn,
                'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': value}}]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'FACT_EDIT_AUTHORIZATION' in str(info):
            value: dict[str, Any] = {'action': 'forget', 'scope': 'parcel code SECRET-1 and page offset 1717 and their copies', 'replacement': '', 'question': ''}
        elif 'FACT_EDIT_REDACTION' in str(info):
            data = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            value = {'edits': [{'segment_id': s['id'], 'quote': quote}
                               for s in data['segments'] for quote in ['SECRET-1', '1717', 'Forget the parcel code and page offset.'] if quote in s['text']]}
        elif 'FACT_EDIT_VERIFICATION' in str(info):
            value = {'safe': True}
        else:
            if turn == 1:
                if any(isinstance(p, ToolReturnPart) for p in messages[-1].parts):
                    return ModelResponse(parts=[TextPart('I have read the policy.')])
                return ModelResponse(parts=[ToolCallPart('read_page', {'url': 'https://example.com/SECRET-1/policy', 'offset': 1717},
                                                        provider_details={'private_duplicate': 'SECRET-1'})],
                                     provider_details={'private_duplicate': 'SECRET-1'})
            if turn == 2:
                return ModelResponse(parts=[ToolCallPart('edit_conversation_fact', {'target': 'parcel code'})])
            assert 'SECRET-1' not in str(messages)
            calls = {p.tool_call_id for m in messages for p in m.parts if isinstance(p, ToolCallPart)}
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            assert returns and all(p.tool_call_id in calls for p in returns)
            assert 'Refunds within 30 days.' in str(returns)
            payloads = [dict(p.content) for p in returns if isinstance(p.content, dict)]
            assert len(payloads) == len(returns)
            assert all(p['offset'] is None for p in payloads)
            assert all(p['truncated'] is False for p in payloads)
            return ModelResponse(parts=[TextPart('Refunds within 30 days.')])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])

    async def run() -> None:
        async with (
            httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http,
            httpx2.AsyncClient(base_url='https://firecrawl.invalid/', transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json={'success': True, 'data': {'markdown': ('x' * 1717) + 'Parcel code SECRET-1. Refunds within 30 days.'}}))) as web,
        ):
            for _ in turns:
                conversation = ConversationService('tool-evidence-owner', ResearchAgent(FunctionModel(model), Firecrawl(web)))
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                with service.state.locked():
                    await service.poll_once()
    asyncio.run(run())
    assert 'forgotten locally' in replies[1]
    assert replies[2] == 'Refunds within 30 days.'
    record = ConversationRecord.objects.get(identity='tool-evidence-owner')
    assert 'SECRET-1' not in json.dumps(record.archive) + json.dumps(record.history)
    assert 'Refunds within 30 days.' in json.dumps(record.archive)

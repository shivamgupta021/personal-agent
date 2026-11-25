import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from conversations.cleanup import TraceCleanup
from core.tracing import tracing
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from conversations.models import ConversationRecord
from conversations.service import ConversationService
from preferences.models import SavedPreference
from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl
from telegram_bot.client import TelegramClient, TelegramError
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('mode', ['normal', 'delivery_failure', 'unsafe_forget', 'casual', 'approved', 'ambiguous', 'forged_quote'])
def test_preference_lifecycle_through_telegram(tmp_path: Path, mode: str) -> None:
    first = 'Remember that I prefer ultrabrief answers.'
    if mode == 'casual':
        first = 'For this answer only, be ultrabrief.'
    turns = [first, 'What preferences are saved?', 'For this answer only, be detailed.',
             'Change my saved answer style to detailed.', 'What preferences are saved?',
             'Forget my saved answer style.', 'What preferences are saved?']
    if mode in ('approved', 'ambiguous'):
        turns = ['Suggest a preference.', 'Yes, save that.', *turns[1:]]
    replies: list[str] = []
    turn = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{'update_id': turn, 'message': {
                'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': turns[turn - 1]}}]})
        replies.append(json.loads(request.content)['text'])
        if mode == 'delivery_failure' and turn == 1:
            raise httpx2.ReadTimeout('controlled delivery failure', request=request)
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = turns[turn - 1]
        if 'PREFERENCE_AUTHORIZATION' in str(info):
            data = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            assert data['latest_user_request'] == request
            action = 'list' if request.startswith('What') else 'forget' if request.startswith('Forget') else 'save'
            if mode == 'casual' and turn == 1 or mode == 'ambiguous' and turn == 2:
                action = 'clarify'
            value: dict[str, Any] = {'action': action, 'key': 'answer_style', 'value': 'detailed' if request.startswith('Change') else 'ultrabrief',
                'consent_quote': 'fabricated' if mode == 'forged_quote' and turn == 1 else request,
                'value_quote': 'detailed' if request.startswith('Change') else 'ultrabrief',
                'suggestion_quote': 'Save ultrabrief answers as your usual preference?' if request.startswith('Yes') else '',
                'question': 'Should this be a lasting preference?'}
        elif 'FACT_EDIT_REDACTION' in str(info):
            data = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            value = {'edits': [{'segment_id': s['id'], 'quote': s['text']} for s in data['segments']
                               if any(word in s['text'].lower() for word in ('ultrabrief', 'detailed', 'answer style', 'answer_style', 'save that'))]}
        elif 'FACT_EDIT_VERIFICATION' in str(info):
            value = {'safe': mode != 'unsafe_forget'}
        else:
            if request.startswith('Suggest'):
                return ModelResponse(parts=[TextPart('Save ultrabrief answers as your usual preference?')])
            if request.startswith('For this') and turn != 1:
                if mode not in ('casual', 'ambiguous', 'forged_quote'):
                    assert '\"value\": \"ultrabrief\"' in str(info)
                return ModelResponse(parts=[TextPart('A detailed answer for this turn only.')])
            if any(isinstance(p, ToolReturnPart) for p in messages[-1].parts):
                return ModelResponse(parts=[TextPart('Should this be a lasting preference?')])
            return ModelResponse(parts=[ToolCallPart('manage_preferences', {})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http, httpx2.AsyncClient() as web:
            for request in turns:
                conversation = ConversationService('preference-owner', ResearchAgent(FunctionModel(model), Firecrawl(web)))
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, lambda turn: (conversation.run) (turn.text), identity='test-bot:7')
                try:
                    await service.poll_once()
                except TelegramError:
                    assert mode == 'delivery_failure' and turn == 1
                if mode == 'approved' and turn == 2:
                    saved = await SavedPreference.objects.aget(identity='preference-owner')
                    assert saved.source_suggestion_id and saved.source_message_id
                if request.startswith('For this') and turn != 1:
                    values = [row.value async for row in SavedPreference.objects.filter(identity='preference-owner')]
                    assert values == ([] if mode in ('casual', 'ambiguous', 'forged_quote') else ['ultrabrief'])
    asyncio.run(run())
    if mode == 'unsafe_forget':
        assert list(SavedPreference.objects.values_list('value', flat=True)) == ['detailed']
        assert 'Nothing changed' in replies[-2]
    else:
        assert not SavedPreference.objects.exists()
        assert 'No saved preferences' in replies[-1]
        record = ConversationRecord.objects.get(identity='preference-owner')
        assert 'ultrabrief' not in json.dumps(record.archive) + json.dumps(record.history), record.archive
        assert 'detailed' not in json.dumps(record.archive) + json.dumps(record.history)
    assert 'detailed' in replies[-3]


@pytest.mark.django_db(transaction=True)
def test_preferences_are_independent_idempotent_and_preserve_unrelated_content(tmp_path: Path) -> None:
    turns = ['Remember that I prefer ultrabrief answers. My favourite colour is teal.',
             'Remember that I prefer ultrabrief answers. My favourite colour is teal.',
             'Remember that I prefer metric units.', 'delete this entire conversation',
             'List my preferences.', 'Forget my saved answer style.']
    turn = 0
    replies: list[str] = []

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            turn += 1
            update = {'update_id': turn, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': turns[turn - 1]}}
            # Telegram may repeat the same delivery in a batch; cursor rejects it.
            return httpx2.Response(200, json={'ok': True, 'result': [update, update]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = turns[turn - 1]
        if 'PREFERENCE_AUTHORIZATION' in str(info):
            metric = 'metric' in request
            value: dict[str, Any] = {'action': 'list' if request.startswith('List') else 'forget' if request.startswith('Forget') else 'save',
                'key': 'units' if metric else 'answer_style', 'value': 'metric' if metric else 'ultrabrief',
                'consent_quote': request, 'value_quote': 'metric' if metric else 'ultrabrief'}
        elif 'FACT_EDIT_REDACTION' in str(info):
            payload = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            value = {'edits': [{'segment_id': s['id'], 'quote': quote} for s in payload['segments'] for quote in ['Forget my saved answer style.', 'answer style: ultrabrief'] if quote in s['text']]}
        elif 'FACT_EDIT_VERIFICATION' in str(info):
            value = {'safe': True}
        else:
            return ModelResponse(parts=[ToolCallPart('manage_preferences', {})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http, httpx2.AsyncClient() as web:
            for _ in turns:
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7,
                    lambda turn: (ConversationService('independent-owner', ResearchAgent(FunctionModel(model), Firecrawl(web))).run) (turn.text), identity='test-bot:7')
                await service.poll_once()
                if turn == 2:
                    saved = [row async for row in SavedPreference.objects.all()]
                    assert len(saved) == 1
                    assert saved[0].source_message_id
                if turn == 4:
                    assert await SavedPreference.objects.acount() == 2
    asyncio.run(run())
    assert len(replies) == len(turns)
    assert 'ultrabrief' in replies[4] and 'metric' in replies[4]
    assert list(SavedPreference.objects.values_list('key', 'value')) == [('units', 'metric')]
    assert 'ultrabrief' not in json.dumps(ConversationRecord.objects.get(identity='independent-owner').archive)


@pytest.mark.django_db(transaction=True)
def test_preference_forgetting_blocks_late_save_and_retries_trace_cleanup() -> None:
    from_marker = 'Remember that I prefer ultrabrief answers.'
    tracing.configure(InMemorySpanExporter())
    deleted: list[str] = []
    failures = True

    def langfuse(request: httpx2.Request) -> httpx2.Response:
        assert request.method == 'DELETE' and not request.content
        deleted.append(request.url.path)
        return httpx2.Response(503 if failures else 202)

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'PREFERENCE_AUTHORIZATION' in str(info):
            payload = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            request = payload['latest_user_request']
            value: dict[str, Any] = {'action': 'forget' if request.startswith('Forget') else 'save', 'key': 'answer_style', 'value': 'ultrabrief', 'value_quote': 'ultrabrief', 'consent_quote': request}
        elif 'FACT_EDIT_REDACTION' in str(info):
            payload = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            value = {'edits': [{'segment_id': s['id'], 'quote': s['text']} for s in payload['segments'] if 'ultrabrief' in s['text'] or 'Forget' in s['text']]}
        elif 'FACT_EDIT_VERIFICATION' in str(info):
            value = {'safe': True}
        else:
            return ModelResponse(parts=[ToolCallPart('manage_preferences', {})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])

    async def run() -> None:
        nonlocal failures
        started = asyncio.Event()
        resume = asyncio.Event()

        async def stale_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if 'PREFERENCE_AUTHORIZATION' not in str(info):
                started.set()
                await resume.wait()
            return model(messages, info)

        async with httpx2.AsyncClient() as web, httpx2.AsyncClient(base_url='https://langfuse.invalid/', transport=httpx2.MockTransport(langfuse)) as lf:
            cleanup = TraceCleanup(lf).run
            conversation = ConversationService('race-preferences', ResearchAgent(FunctionModel(model), Firecrawl(web)), cleanup)
            assert 'Saved preference' in await conversation.run(from_marker)
            stale = asyncio.create_task(ConversationService('race-preferences', ResearchAgent(FunctionModel(stale_model), Firecrawl(web)), cleanup).run(from_marker))
            await started.wait()
            result = await conversation.run('Forget my saved answer style.')
            assert 'forgotten locally' in result and 'failed' in result
            resume.set()
            assert 'state changed' in await stale
            assert await SavedPreference.objects.acount() == 0
            record = await ConversationRecord.objects.aget(identity='race-preferences')
            assert 'ultrabrief' not in json.dumps(record.archive) + json.dumps(record.history), record.archive
            first_deletes = set(deleted)
            failures = False
            assert 'deletion requested' in await conversation.run('retry trace cleanup')
            assert first_deletes == set(deleted)
    try:
        asyncio.run(run())
    finally:
        tracing.shutdown()


@pytest.mark.django_db(transaction=True)
def test_intervening_inspection_invalidates_older_suggestion() -> None:
    request = ''
    seen: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'PREFERENCE_AUTHORIZATION' in str(info):
            payload = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            if request.startswith('Yes'):
                seen.append(payload['latest_assistant'])
                assert payload['latest_assistant'] == 'No saved preferences.'
            value = {'action': 'list' if request.startswith('List') else 'clarify', 'question': 'Which preference should I save?'}
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])
        if request.startswith('Suggest'):
            return ModelResponse(parts=[TextPart('Would you like me to save ultrabrief answers as your preference?')])
        if any(isinstance(p, ToolReturnPart) for p in messages[-1].parts):
            return ModelResponse(parts=[TextPart('Which preference should I save?')])
        return ModelResponse(parts=[ToolCallPart('manage_preferences', {})])

    async def run() -> None:
        nonlocal request
        async with httpx2.AsyncClient() as web:
            for request in ['Suggest a preference.', 'List preferences.', 'Yes, save that.']:
                await ConversationService('intervening-list', ResearchAgent(FunctionModel(model), Firecrawl(web))).run(request)
    asyncio.run(run())
    assert seen == ['No saved preferences.']
    assert not SavedPreference.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_fact_forgetting_sanitizes_preference_source_without_deleting_preference() -> None:
    request = ''

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'PREFERENCE_AUTHORIZATION' in str(info):
            value: dict[str, Any] = {'action': 'save', 'key': 'units', 'value': 'metric', 'value_quote': 'metric', 'consent_quote': 'Remember metric units.'}
        elif 'FACT_EDIT_AUTHORIZATION' in str(info):
            value = {'action': 'forget', 'scope': 'phone number PHONE-765', 'replacement': ''}
        elif 'FACT_EDIT_REDACTION' in str(info):
            payload = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
            value = {'edits': [{'segment_id': s['id'], 'quote': quote} for s in payload['segments']
                               for quote in ['My phone is PHONE-765.', 'Forget my phone.'] if quote in s['text']]}
        elif 'FACT_EDIT_VERIFICATION' in str(info):
            value = {'safe': True}
        else:
            return ModelResponse(parts=[ToolCallPart('edit_conversation_fact', {'target': 'phone'})] if request.startswith('Forget') else [ToolCallPart('manage_preferences', {})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])

    async def run() -> None:
        nonlocal request
        async with httpx2.AsyncClient() as web:
            for request in ['Remember metric units. My phone is PHONE-765.', 'Forget my phone.']:
                await ConversationService('source-forgetting', ResearchAgent(FunctionModel(model), Firecrawl(web))).run(request)
    asyncio.run(run())
    saved = SavedPreference.objects.get()
    record = ConversationRecord.objects.get(identity='source-forgetting')
    assert saved.value == 'metric'
    assert saved.source_message_id in record.archive
    assert 'PHONE-765' not in json.dumps(record.archive) + json.dumps(record.history)
    assert 'PHONE-765' not in str(SavedPreference.objects.values().get())
    assert 'metric' in json.dumps(record.archive[saved.source_message_id])


@pytest.mark.django_db(transaction=True)
def test_successful_preference_confirmation_has_no_error_span() -> None:
    exporter = InMemorySpanExporter()
    tracing.configure(exporter)

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'PREFERENCE_AUTHORIZATION' in str(info):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'action': 'save', 'key': 'units', 'value': 'metric',
                'value_quote': 'metric', 'consent_quote': 'Remember metric units.'})])
        return ModelResponse(parts=[ToolCallPart('manage_preferences', {})])

    async def run() -> None:
        async with httpx2.AsyncClient() as web:
            service = ConversationService('trace-preference', ResearchAgent(FunctionModel(model), Firecrawl(web)))
            assert 'Saved preference' in await service.run('Remember metric units.')

    try:
        asyncio.run(run())
        tracing.flush()
        spans = [span for span in exporter.get_finished_spans() if span.name == 'conversation.turn']
        assert len(spans) == 1
        assert spans[0].status.status_code.name != 'ERROR'
    finally:
        tracing.shutdown()

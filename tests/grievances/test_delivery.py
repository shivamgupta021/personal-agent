import asyncio
import json
from pathlib import Path

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials, OpenAIChatGPTProvider

from conversations.service import ConversationService
from research.service import ResearchService
from subscriptions.service import SubscriptionService
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn
from grievances.service import CaseService
from core.runtime import SubscriptionApplicationService
from telegram_bot.client import TelegramClient, TelegramError
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState


@pytest.mark.django_db(transaction=True)
def test_failed_reply_preserves_committed_case_and_normalized_replay(tmp_path: Path) -> None:
    observed: list[OwnerTurn] = []
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
            {'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case'})])
    def telegram(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith('getUpdates'):
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': 42, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'},
                                           'text': 'Create a case called Kettle'},
            }]})
        raise httpx2.ReadTimeout('uncertain delivery', request=request)
    async def run() -> None:
        application = CaseService(FunctionModel(respond))
        async def handle(turn: OwnerTurn) -> str:
            observed.append(turn)
            return await application.run(turn)
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http:
            transport = TelegramService(TelegramClient(http), PollingState(tmp_path), 7,
                                        turn_handler=handle, identity='bot:7')
            with pytest.raises(TelegramError):
                await transport.poll_once()
        assert observed == [OwnerTurn('bot:7', 'bot:7', 'telegram:42', 'Create a case called Kettle')]
        replay = await CaseService(FunctionModel(respond)).run(observed[0])
        assert 'already processed' in replay
        assert len(await application.store.list_cases('bot:7')) == 1
        assert PollingState(tmp_path).offset() == 43
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_simultaneous_same_delivery_creates_once_and_stale_selection_cannot_write() -> None:
    async def run() -> None:
        both_started = asyncio.Event()
        release = asyncio.Event()
        count = 0
        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            nonlocal count
            count += 1
            if count == 2:
                both_started.set()
            await release.wait()
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                {'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case'})])
        service = CaseService(FunctionModel(respond))
        turn = OwnerTurn('owner', 'chat', 'one-delivery', 'Create a case called Kettle')
        tasks = [asyncio.create_task(service.run(turn)) for _ in range(2)]
        await asyncio.wait_for(both_started.wait(), 5)
        release.set()
        replies = await asyncio.gather(*tasks)
        assert len(await service.store.list_cases('owner')) == 1
        assert any('already processed' in reply for reply in replies)
        # Pause an update while another normalized turn leaves the selected case.
        started = asyncio.Event()
        finish = asyncio.Event()
        async def update(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            started.set()
            await finish.wait()
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                {'action': 'update', 'facts': [{'key': 'issue', 'value': 'broken', 'quote': 'broken'}]})])
        pending = asyncio.create_task(CaseService(FunctionModel(update)).run(
            OwnerTurn('owner', 'chat', 'update', 'It arrived broken')))
        await started.wait()
        def leave(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                {'action': 'leave', 'intent_quote': 'Return to general research'})])
        await CaseService(FunctionModel(leave)).run(OwnerTurn('owner', 'chat', 'leave', 'Return to general research'))
        finish.set()
        assert 'selection changed' in (await pending).lower()
        case = await service.store.get_case('owner', (await service.store.list_cases('owner'))[0].id)
        assert case and case.facts == {}
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_production_public_handoff_releases_real_subscription_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests = 0
    def model_http(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        payload = json.loads(request.content)
        if requests == 1:
            output = [{'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1',
                       'name': payload['tools'][0]['name'], 'arguments': json.dumps({'specialist': 'research'})}]
        else:
            output = [{'type': 'message', 'id': 'msg_2', 'role': 'assistant', 'status': 'completed',
                       'content': [{'type': 'output_text', 'text': 'Public answer from subscription', 'annotations': []}]}]
        response = {'id': f'resp_{requests}', 'object': 'response', 'created_at': 1,
            'model': 'test-model', 'status': 'completed', 'output': output,
            'usage': {'input_tokens': 5, 'output_tokens': 5, 'total_tokens': 10}}
        events = [{'type': 'response.created', 'response': response, 'sequence_number': 0},
                  {'type': 'response.output_item.done', 'item': output[0], 'output_index': 0, 'sequence_number': 1}]
        if requests > 1:
            events.append({'type': 'response.output_text.delta', 'item_id': 'msg_2', 'output_index': 0,
                           'content_index': 0, 'delta': 'Public answer from subscription', 'sequence_number': 2})
            events.append({'type': 'response.output_text.done', 'item_id': 'msg_2', 'output_index': 0,
                           'content_index': 0, 'text': 'Public answer from subscription', 'sequence_number': 2})
        events.append({'type': 'response.completed', 'response': response, 'sequence_number': 3})
        body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events)
        return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, content=body)
    async def run() -> None:
        subscription = SubscriptionService(tmp_path)
        tmp_path.mkdir(exist_ok=True)
        await subscription.session.credentials.save(OpenAIChatGPTCredentials(client_id='synthetic', access_token='synthetic', refresh_token='synthetic'))
        def provider() -> OpenAIChatGPTProvider:
            client = AsyncOpenAI(api_key='synthetic', base_url='https://subscription.invalid/',
                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(model_http)))
            return OpenAIChatGPTProvider(openai_client=client)
        monkeypatch.setattr(subscription.session, 'provider', provider)
        public = ConversationService('owner', ResearchService(subscription))
        application = SubscriptionApplicationService(subscription, public.run)
        answer = await application.run(OwnerTurn('owner', 'owner', 'public-1', 'Explain Python generators'))
        assert answer == 'Public answer from subscription'
        assert requests == 2
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_failed_delivery_preserves_draft_and_replay_does_not_regenerate(tmp_path: Path) -> None:
    drafting = False
    generations = 0
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal generations
        if 'fact_order' in info.output_tools[0].parameters_json_schema.get('properties', {}):
            generations += 1
            result: dict[str, object] = {'fact_order': ['issue']}
        elif drafting:
            result = {'action': 'draft'}
        else:
            result = {'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case',
                      'facts': [{'key': 'issue', 'value': 'broken', 'quote': 'broken'}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])
    def telegram(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith('getUpdates'):
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': 43, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'},
                                           'text': 'Draft a refund email'},
            }]})
        raise httpx2.ReadTimeout('uncertain delivery', request=request)
    async def run() -> None:
        nonlocal drafting
        application = CaseService(FunctionModel(respond))
        await application.run(OwnerTurn('bot:7', 'bot:7', 'create', 'Create a case called Kettle. It is broken.'))
        case_id = (await application.store.list_cases('bot:7'))[0].id
        drafting = True
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http:
            transport = TelegramService(TelegramClient(http), PollingState(tmp_path), 7,
                                        turn_handler=application.run, identity='bot:7')
            with pytest.raises(TelegramError):
                await transport.poll_once()
        restarted = CaseService(FunctionModel(respond))
        assert 'version 1' in await restarted.run(OwnerTurn('bot:7', 'bot:7', 'telegram:43', 'Draft a refund email'))
        assert len(await restarted.store.list_drafts('bot:7', case_id)) == 1
        assert generations == 1 and PollingState(tmp_path).offset() == 44
    asyncio.run(run())

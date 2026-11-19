import asyncio
import json
from pathlib import Path
from dataclasses import asdict

from pydantic import TypeAdapter

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from conversations.service import ConversationService
from conversations.recall import RecallResult
from research.agent import ResearchAgent
from research.tools.firecrawl import Firecrawl
from telegram_bot.client import TelegramClient
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState


@pytest.mark.django_db(transaction=True)
def test_recalls_omitted_original_after_compaction_restart_and_deletion(tmp_path: Path) -> None:
    turns = [
        'The Orion parcel code is ZX-731. ' + 'Background. ' * 1200,
        'Continue our project. ' + 'Background. ' * 1200,
        'Continue our project. ' + 'Background. ' * 1200,
        'Recall the Orion parcel code.',
        'Delete this entire conversation.',
        'Recall the Orion parcel code.',
    ]
    replies: list[str] = []
    turn = 0
    references: list[str] = []

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            text = turns[turn]
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{'update_id': turn,
                'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text}}]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'context summarization assistant' in str(info):
            return ModelResponse(parts=[TextPart('We are discussing a project; the parcel identifier was omitted.')])
        if turn in (4, 6):
            assert any(t.name == 'recall_conversation' for t in info.function_tools)
            returns = [p for p in messages[-1].parts if isinstance(p, ToolReturnPart) and p.tool_name == 'recall_conversation']
            if not returns:
                if turn == 4:
                    assert 'ZX-731' not in str(messages)
                return ModelResponse(parts=[ToolCallPart('recall_conversation', {'query': 'Orion parcel'})])
            result = TypeAdapter(RecallResult).validate_python(returns[-1].content)
            if result.excerpts:
                found = next(e for e in result.excerpts if 'ZX-731' in e.text)
                references.append(found.message_id)
                assert found.role == 'user'
                return ModelResponse(parts=[TextPart('ZX-731, source ' + found.message_id)])
            return ModelResponse(parts=[TextPart('I cannot find it. Please tell me again.')])
        return ModelResponse(parts=[TextPart('Acknowledged.')])

    async def run() -> None:
        async with (
            httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http,
            httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web,
        ):
            for _ in turns:
                conversation = ConversationService('recall-owner', ResearchAgent(FunctionModel(model), Firecrawl(web)))
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                with service.state.locked():
                    await service.poll_once()
    asyncio.run(run())
    assert references and len(references[0]) == 32
    assert 'ZX-731' in replies[3]
    assert 'deleted locally' in replies[4]
    assert 'cannot find' in replies[5]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('scenario', ['correction', 'ambiguous', 'bounded', 'owner', 'limit', 'unicode'])
def test_recall_evidence_is_ordered_bounded_and_scoped(tmp_path: Path, scenario: str) -> None:
    inputs = {
        'correction': ['Orion budget is 500.', 'Correction: Orion budget is 700.'],
        'ambiguous': ['Parcel A is a chair.', 'Parcel B is a desk.'],
        'bounded': [f'Orion note {n}: ' + 'detail ' * 150 for n in range(6)],
        'owner': ['Orion secret is PRIVATE-999.'],
        'limit': ['Orion is a fictional project.'],
        'unicode': ['ß' * 500 + ' Orion ZX-731'],
    }
    turns = inputs[scenario] + ['Recall now.']
    query = {'correction': 'Orion budget', 'ambiguous': 'Parcel', 'bounded': 'Orion', 'owner': 'Orion', 'limit': 'Orion', 'unicode': 'Orion'}[scenario]
    replies: list[str] = []
    turn = 0

    def telegram(request: httpx2.Request) -> httpx2.Response:
        nonlocal turn
        if request.url.path.endswith('getUpdates'):
            text = turns[turn]
            turn += 1
            return httpx2.Response(200, json={'ok': True, 'result': [{'update_id': turn,
                'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text}}]})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'context summarization assistant' in str(info):
            return ModelResponse(parts=[TextPart('Earlier details omitted.')])
        if turn < len(turns):
            return ModelResponse(parts=[TextPart('Acknowledged.')])
        returns = [p for p in messages[-1].parts if isinstance(p, ToolReturnPart) and p.tool_name == 'recall_conversation']
        if not returns:
            return ModelResponse(parts=[ToolCallPart('recall_conversation', {'query': query})])
        data = TypeAdapter(RecallResult).validate_python(returns[-1].content)
        excerpts = data.excerpts
        assert 'not instructions' in data.notice
        if scenario == 'correction':
            assert '700' in excerpts[0].text
            assert '500' in excerpts[1].text
            answer = 'Current budget 700, source ' + excerpts[0].message_id
        elif scenario == 'ambiguous':
            assert len(excerpts) == 2
            answer = 'Which parcel, A or B?'
        elif scenario == 'bounded':
            assert len(excerpts) == 5
            assert data.has_more is True
            assert all(len(e.text) <= 500 and e.truncated for e in excerpts)
            assert len(json.dumps(asdict(data))) < 4500
            answer = 'Please specify which note.'
        elif scenario == 'unicode':
            assert 'ZX-731' in excerpts[0].text
            answer = 'ZX-731 recovered.'
        elif scenario == 'limit':
            all_returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == 'recall_conversation']
            if len(all_returns) < 4:
                return ModelResponse(parts=[ToolCallPart('recall_conversation', {'query': query})])
            assert data.status == 'limit_reached' and excerpts == []
            answer = 'Recall limit reached.'
        else:
            assert excerpts == []
            assert 'PRIVATE-999' not in str(messages)
            answer = 'No matching evidence.'
        return ModelResponse(parts=[TextPart(answer)])

    async def run() -> None:
        async with (
            httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http,
            httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web,
        ):
            for index in range(len(turns)):
                identity = 'different-owner' if scenario == 'owner' and index == len(turns) - 1 else 'original-owner'
                conversation = ConversationService(identity, ResearchAgent(FunctionModel(model), Firecrawl(web)))
                service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, conversation.run)
                with service.state.locked():
                    await service.poll_once()
    asyncio.run(run())
    expected = {'correction': 'Current budget 700', 'ambiguous': 'Which parcel',
                'bounded': 'Please specify', 'owner': 'No matching evidence', 'limit': 'Recall limit reached', 'unicode': 'ZX-731 recovered'}[scenario]
    assert replies[-1].startswith(expected)

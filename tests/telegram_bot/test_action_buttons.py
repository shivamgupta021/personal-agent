"""Buttons are authenticated, case-bound actions, not model-proposed permission."""
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx2
import pytest

from core.actions import CaseAction
from core.service import ApplicationService
from core.turns import OwnerTurn
from gmail.models import CaseFollowup
from grievances.service import CaseService
from pydantic_ai.models.function import FunctionModel
from telegram_bot.client import TelegramClient
from telegram_bot.presentation import ReplyPresentation
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState
from tests.gmail.test_followups import FollowupHarness
from tests.telegram_bot.test_gmail import AssistanceHarness


@pytest.mark.django_db(transaction=True)
def test_case_access_is_one_prompt_and_button_resumes(tmp_path: Path) -> None:
    h = AssistanceHarness(tmp_path)
    async def scenario() -> None:
        h.text = 'Help me get my Kettle refund'
        first = await CaseService(FunctionModel(h.model), correspondence=h.correspondence).run(OwnerTurn('test:7', 'test:7', 'first', h.text))
        assert 'Plan for Kettle' in first
        assert 'I agree to' not in first
        assert h.searches == 0
        token = re.search(r'action://([^)]*)', first)
        assert token
        h.text = 'Help me get my Kettle refund'
        answer = await CaseService(FunctionModel(h.model), correspondence=h.correspondence).run(OwnerTurn('test:7', 'test:7', 'button', 'Continue with Gmail', callback=token[1]))
        assert h.searches == 1
        assert 'does not confirm a refund' in answer
    try:
        asyncio.run(scenario())
    finally:
        h.http.close()


@pytest.mark.django_db(transaction=True)
def test_approval_button_binds_generation_and_never_resends() -> None:
    h = FollowupHarness()
    proposal = h.say('Prepare automatic sending')
    token = re.search(r'action://([^)]*)', proposal)
    assert token
    app = ApplicationService(h.model, correspondence=h.app.correspondence)
    async def approve(delivery: str) -> str:
        return await app.run(OwnerTurn('bot:123', 'chat', delivery, 'Approve & send', callback=token[1]))
    assert 'approved' in asyncio.run(approve('click1'))
    assert 'another email' in asyncio.run(approve('click2'))
    assert CaseFollowup.objects.get(case=h.case).status == 'active'


@pytest.mark.django_db(transaction=True)
def test_old_approval_button_cannot_approve_replacement_plan() -> None:
    h = FollowupHarness()
    proposal = h.say('Prepare automatic sending')
    token = re.search(r'action://([^)]*)', proposal)
    assert token
    h.say('Prepare automatic sending')
    answer = asyncio.run(h.app.run(OwnerTurn('bot:123', 'chat', 'old-click', 'Approve', callback=token[1])))
    assert 'changed' in answer
    assert CaseFollowup.objects.get(case=h.case).status == 'proposed'


def test_button_signature_and_formatting() -> None:
    action = CaseAction('g', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    token = action.token('owner', 2)
    assert len(token.encode()) <= 64
    assert CaseAction.verify(token, 'owner', 2) == action
    assert CaseAction.verify(token, 'intruder', 2) is None
    assert CaseAction.verify(token, 'owner', 3) is None
    rendered = ReplyPresentation.formatted('📎 **Receipt added** <not HTML>')
    assert rendered['text'] == '📎 Receipt added <not HTML>'
    assert rendered['entities'] == [{'type': 'bold', 'offset': 3, 'length': 13}]


@pytest.mark.parametrize('owner', [7, 9])
def test_telegram_callback_authentication(tmp_path: Path, owner: int) -> None:
    turns: list[OwnerTurn] = []
    requests: list[str] = []
    async def handle(turn: OwnerTurn) -> str:
        turns.append(turn)
        return '**Done**'
    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        result: Any = {}
        if request.url.path.endswith('getUpdates'):
            result = [{'update_id': 1, 'callback_query': {'id': 'c1', 'data': 'button-token',
                'from': {'id': owner}, 'message': {'from': {'id': 12345},
                'chat': {'id': 7, 'type': 'private'}}}}]
        return httpx2.Response(200, json={'ok': True, 'result': result})
    async def scenario() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(respond)) as http:
            await TelegramService(TelegramClient(http), PollingState(tmp_path), 7, handle, identity='owner').poll_once()
    asyncio.run(scenario())
    assert len(turns) == (1 if owner == 7 else 0)
    if turns:
        assert turns[0].callback == 'button-token'
        assert '/answerCallbackQuery' in requests

"""Conversational consent uses the desktop connection boundary, not a terminal."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn
from gmail.errors import GmailError, GmailDisconnected
from gmail.factory import GmailFactory
from gmail.onboarding import GmailOnboarding, ConnectionAttempt
from gmail.service import GmailService
from grievances.service import SubscriptionApplicationService
from subscriptions.service import SubscriptionService
from tests.telegram_bot.test_gmail import DraftHarness
from gmail.constants import SCOPES
from gmail.models import CaseMailbox


@pytest.mark.parametrize('failure', [False, True])
def test_desktop_consent_progress_and_sanitized_result(tmp_path: Path, failure: bool) -> None:
    client_file = tmp_path / 'client.json'
    client_file.write_text('{}')
    service = Mock(spec=GmailService)
    if failure:
        service.connect.side_effect = GmailError('SECRET provider payload')
    else:
        service.connect.return_value = 'Connected Gmail: owner@example.com.'
    progress: list[str] = []

    async def send(message: str) -> None:
        progress.append(message)

    result = asyncio.run(GmailOnboarding(lambda: service, client_file, send).connect(
        OwnerTurn('owner', 'owner', 'consent', 'Connect Gmail for owner@example.com'),
        'owner@example.com', False))
    assert result.connected is not failure
    assert len(progress) == 1 and 'on this Mac' in progress[0]
    assert 'SECRET' not in result.message
    service.connect.assert_called_once_with(client_file, 'owner@example.com', compose=False)


@pytest.mark.django_db(transaction=True)
def test_connection_runs_after_releasing_model_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    leased = False
    connections: list[tuple[str, bool]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'action': 'gmail_connect', 'gmail_account': 'owner@example.com',
            'intent_quote': 'Connect Gmail for owner@example.com', 'gmail_compose': False})])

    @asynccontextmanager
    async def model(self: SubscriptionService, name: str = '') -> AsyncIterator[Model]:
        nonlocal leased
        leased = True
        try:
            yield FunctionModel(respond)
        finally:
            leased = False

    async def connect(turn: OwnerTurn, email: str, compose: bool) -> ConnectionAttempt:
        assert not leased
        connections.append((email, compose))
        return ConnectionAttempt(True, 'Gmail connected.')

    async def public(message: str) -> str:
        raise AssertionError('No public research during consent')

    monkeypatch.setattr(SubscriptionService, 'model', model)
    service = SubscriptionApplicationService(SubscriptionService(tmp_path), public, connect)
    reply = asyncio.run(service.run(OwnerTurn('owner', 'owner', 'connect', 'Connect Gmail for owner@example.com')))
    assert 'Gmail connected' in reply
    assert connections == [('owner@example.com', True)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('connection_unavailable', [False, True])
def test_missing_connection_saves_goal_without_planning_and_resumes_after_consent(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connection_unavailable: bool) -> None:
    connected = False
    routed: list[str] = []
    original = 'Help me with my Hosteller refund'

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        payload = json.loads(next(p.content for p in messages[-1].parts
            if isinstance(p, UserPromptPart) and isinstance(p.content, str)))
        request = payload['current_request']
        routed.append(request)
        if request.startswith('Connect'):
            action = {'action': 'gmail_connect', 'gmail_account': 'owner@example.com', 'intent_quote': request}
        elif connected:
            action = {'action': 'show'}
        else:
            action = {'action': 'case_help', 'label': 'Hosteller', 'intent_quote': request}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, action)])

    @asynccontextmanager
    async def model(self: SubscriptionService, name: str = '') -> AsyncIterator[Model]:
        yield FunctionModel(respond)

    def require_connection() -> None:
        if connection_unavailable:
            raise GmailError('SECRET Keychain failure')
        if not connected:
            raise GmailDisconnected('Missing local grant')

    async def connect(turn: OwnerTurn, email: str, compose: bool) -> ConnectionAttempt:
        nonlocal connected
        connected = True
        return ConnectionAttempt(True, 'Gmail connected.')

    async def public(message: str) -> str:
        raise AssertionError('No public research')

    monkeypatch.setattr(SubscriptionService, 'model', model)
    monkeypatch.setattr(GmailFactory, 'require_connection', require_connection)
    service = SubscriptionApplicationService(SubscriptionService(tmp_path), public, connect)

    async def scenario() -> None:
        first = await service.run(OwnerTurn('owner', 'owner', 'first', original))
        if connection_unavailable:
            assert 'could not check the Gmail connection' in first
            assert 'SECRET' not in first
            assert routed == [original]
            return
        assert 'Connect Gmail' in first
        assert routed == [original]  # No mail access or query planning before the button.
        token = re.search(r'action://([^)]*)', first)
        assert token is not None
        reply = await service.run(OwnerTurn('owner', 'owner', 'connect', 'Connect Gmail', callback=token[1]))
        assert 'I agree to' not in reply
        assert original in routed[1:]  # Successful connection resumes without a magic phrase.
        assert 'Hosteller' in reply

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_readonly_draft_request_is_saved_for_consent_resume(tmp_path: Path) -> None:
    harness = DraftHarness(tmp_path)

    async def scenario() -> None:
        await harness.send('I agree to email processing')
        await harness.send('Help me get my Kettle refund')
        assert harness.credentials.connection is not None
        harness.credentials.connection.credentials._scopes = list(SCOPES)
        request = 'Draft the reply in Gmail'
        answer = await harness.send(request)
        assert 'reading only' in answer
        assert (await CaseMailbox.objects.aget()).pending_goal == request
        assert not harness.created
        original_model = harness.model

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            properties = info.output_tools[0].parameters_json_schema.get('properties', {}) if info.output_tools else {}
            if 'action' in properties and harness.text.startswith('Enable'):
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                    'action': 'gmail_connect', 'gmail_account': 'owner@example.com',
                    'gmail_compose': True, 'intent_quote': harness.text})])
            return original_model(messages, info)

        @asynccontextmanager
        async def model(self: SubscriptionService, name: str = '') -> AsyncIterator[Model]:
            yield FunctionModel(respond)

        async def connect(turn: OwnerTurn, email: str, compose: bool) -> ConnectionAttempt:
            assert compose and email == 'owner@example.com'
            assert harness.credentials.connection is not None
            harness.credentials.connection.credentials._scopes = [*SCOPES, 'https://www.googleapis.com/auth/gmail.compose']
            harness.text = request
            return ConnectionAttempt(True, 'Gmail connected.')

        async def public(message: str) -> str:
            raise AssertionError('Private draft cannot reach research')

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(SubscriptionService, 'model', model)
            patch.setattr(GmailFactory, 'mailbox', harness.correspondence.mailbox)
            patch.setattr(GmailFactory, 'require_connection', harness.connection.require_connection)
            app = SubscriptionApplicationService(SubscriptionService(tmp_path), public, connect)
            harness.text = 'Enable Gmail draft creation for owner@example.com'
            answer = await app.run(OwnerTurn('test:7', 'test:7', 'upgrade', harness.text))
        assert 'Draft v1' in answer
        assert len(harness.created) == 1
        assert (await CaseMailbox.objects.aget()).pending_goal == ''

    try:
        asyncio.run(scenario())
    finally:
        harness.http.close()


@pytest.mark.django_db(transaction=True)
def test_pending_consent_does_not_block_other_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ready = asyncio.Event()
    started = asyncio.Event()
    delivered: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        data = json.loads(str(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart))))
        request = data['current_request']
        result = ({'action': 'gmail_connect', 'gmail_account': 'owner@example.com', 'intent_quote': request}
            if request.startswith('Connect') else {'action': 'conversation', 'conversation_reply': '323'})
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])

    @asynccontextmanager
    async def model(self: SubscriptionService, name: str = '') -> AsyncIterator[Model]:
        yield FunctionModel(respond)

    async def connect(turn: OwnerTurn, email: str, compose: bool) -> ConnectionAttempt:
        started.set()
        await ready.wait()
        return ConnectionAttempt(True, 'Gmail connected.')

    async def public(message: str) -> str:
        raise AssertionError('Unexpected extra model call')

    async def deliver(reply: object) -> None:
        delivered.append(str(reply))

    monkeypatch.setattr(SubscriptionService, 'model', model)
    service = SubscriptionApplicationService(SubscriptionService(tmp_path), public, connect, progress=deliver)

    async def scenario() -> None:
        reply = await asyncio.wait_for(service.run(OwnerTurn('owner', 'owner', 'connect', 'Connect Gmail for owner@example.com')), 3)
        assert 'consent' in reply.lower()
        await started.wait()
        answer = await service.run(OwnerTurn('owner', 'owner', 'other', 'What is 17 times 19?'))
        assert answer == '323'
        ready.set()
        await service.finish_connections()
        assert len(delivered) == 1 and 'Gmail connected.' in delivered[0]

    asyncio.run(scenario())

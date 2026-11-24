"""Conversational consent uses the desktop connection boundary, not a terminal."""

import asyncio
import json
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
    assert connections == [('owner@example.com', False)]


@pytest.mark.django_db(transaction=True)
def test_missing_connection_saves_goal_without_planning_and_resumes_after_consent(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert 'Which Gmail address' in first
        assert routed == [original]  # No Gmail query planning spent on a missing grant.
        reply = await service.run(OwnerTurn('owner', 'owner', 'connect', 'Connect Gmail for owner@example.com'))
        assert 'I agree to email processing' in reply
        assert original not in routed[1:]  # No email processing before its separate acknowledgement.
        reply = await service.run(OwnerTurn('owner', 'owner', 'ack', 'I agree to email processing'))
        assert original in routed[2:]
        assert 'Hosteller' in reply

    asyncio.run(scenario())

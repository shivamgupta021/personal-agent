"""Saved preference reads must not open a model connection."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.models import Model
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from conversations.models import ConversationRecord
from grievances.models import GrievanceCase, CaseSelection, CaseFactRevision

from core.turns import OwnerTurn
from core.service import ApplicationService
from core.runtime import SubscriptionApplicationService
from preferences.models import SavedPreference
from subscriptions.service import SubscriptionService


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('populated', [False, True])
@pytest.mark.parametrize('message', ['What preferences have you saved?', ' SHOW my preferences! ', 'What do you remember about my preferences?'])
def test_list_preferences_without_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, populated: bool, message: str) -> None:
    case = GrievanceCase.objects.create(owner_id='owner', label='Private case', label_key='private')
    CaseSelection.objects.create(owner_id='owner', conversation_id='owner', case=case)
    if populated:
        SavedPreference.objects.create(identity='owner', key='flight_time', value='evening', source_message_id='source')
    SavedPreference.objects.create(identity='other', key='secret', value='other owner data', source_message_id='source')

    @asynccontextmanager
    async def model(self: SubscriptionService, name: str = '') -> AsyncIterator[Model]:
        raise AssertionError('Saved preference inspection must not open a model session')
        yield  # pragma: no cover

    async def public(message: str) -> str:
        raise AssertionError('Preference inspection must not use research')

    monkeypatch.setattr(SubscriptionService, 'model', model)
    service = SubscriptionApplicationService(SubscriptionService(tmp_path), public)
    answer = asyncio.run(service.run(OwnerTurn('owner', 'owner', 'inspect', message)))
    assert answer == ('Saved preferences:\nflight time: evening' if populated else 'No saved preferences.')

    assert CaseSelection.objects.get(owner_id='owner').case_id == case.id
    history = ConversationRecord.objects.get(identity='owner').history
    assert answer in [part.get('content') for item in history for part in item['parts']]
    assert 'other owner data' not in str(history)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('message,route', [
    ('Could you display the settings you remember for me?', 'list_preferences'),
    ('Show my preferences and change my flight time to morning', 'clarify'),
    ('Explain the sentence: "What preferences have you saved?"', 'clarify'),
])
def test_unfamiliar_and_mixed_requests_keep_routing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        message: str, route: str) -> None:
    calls = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist':'preferences'})])
        assert calls == 2
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'action': 'list' if route == 'list_preferences' else 'clarify'})])

    @asynccontextmanager
    async def model(self: SubscriptionService, name: str = '') -> AsyncIterator[Model]:
        yield FunctionModel(respond)

    async def public(message: str) -> str:
        raise AssertionError('No web access for preferences')

    monkeypatch.setattr(SubscriptionService, 'model', model)
    app = SubscriptionApplicationService(SubscriptionService(tmp_path), public)
    answer = asyncio.run(app.run(OwnerTurn('owner', 'owner', 'fallback', message)))
    assert calls == 2
    if route == 'list_preferences':
        assert answer == 'No saved preferences.'
    assert not SavedPreference.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_router_cannot_copy_case_identifiers_into_preference_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = GrievanceCase.objects.create(owner_id='owner', label='Refund', label_key='refund')
    CaseFactRevision.objects.create(case=case, revision=1, facts={'order_reference': 'PRIVATE-123'})

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist': 'preferences'})])

    @asynccontextmanager
    async def model(self: SubscriptionService, name: str = '') -> AsyncIterator[Model]:
        yield FunctionModel(respond)

    async def public(message: str) -> str:
        raise AssertionError('No public access')

    monkeypatch.setattr(SubscriptionService, 'model', model)
    app = SubscriptionApplicationService(SubscriptionService(tmp_path), public)
    answer = asyncio.run(app.run(OwnerTurn('owner', 'owner', 'private', 'Show my saved preferences for order PRIVATE-123')))
    assert 'includes case details' in answer
    assert not any('PRIVATE-123' in str(r.history) for r in ConversationRecord.objects.all())


@pytest.mark.django_db(transaction=True)
def test_explicit_preference_save_routes_directly_to_authorization() -> None:
    calls = 0
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            result = {'specialist': 'preferences'}
        else:
            assert calls == 2, 'No intermediary memory model for an explicit preference operation'
            assert 'consent_quote' in str(info.output_tools[0].parameters_json_schema)
            result = {'action': 'save', 'key': 'reply_style', 'value': 'concise replies',
                      'consent_quote': 'Remember that I prefer concise replies.', 'value_quote': 'concise replies'}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])
    answer = asyncio.run(ApplicationService(FunctionModel(respond)).run(
        OwnerTurn('owner', 'owner', 'save', 'Remember that I prefer concise replies.')))
    assert answer == 'Saved preference: reply style: concise replies.'
    assert SavedPreference.objects.get(identity='owner').value == 'concise replies'
    assert calls == 2

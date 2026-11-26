"""Terminal routine tools through the public application boundary."""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from django.conf import settings
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models import Model

from core.service import ApplicationService
from core.runtime import SubscriptionApplicationService
from core.routine_evaluation import CASES, RoutineExperiment
from core.turns import OwnerTurn
from grievances.models import GrievanceCase, CaseFactRevision, CaseSelection
from preferences.models import SavedPreference
from subscriptions.service import SubscriptionService


@pytest.mark.django_db(transaction=True)
def test_preference_tool_commits_with_one_generation_and_validates_source() -> None:
    calls = []
    request = 'Remember that I prefer plain English.'
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[ToolCallPart('manage_preference', {
            'action': 'save', 'key': 'reply_style', 'value': 'plain English',
            'value_quote': 'plain English', 'consent_quote': request})])
    answer = asyncio.run(ApplicationService(FunctionModel(respond), routine_tools=True).run(
        OwnerTurn('owner', 'owner', 'save', request)))
    assert SavedPreference.objects.get(identity='owner').value == 'plain English'
    assert 'Saved preference' in answer and len(calls) == 1


@pytest.mark.django_db(transaction=True)
def test_invented_preference_value_is_rejected() -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart('manage_preference', {
            'action': 'save', 'key': 'reply_style', 'value': 'invented',
            'value_quote': 'invented', 'consent_quote': 'Remember plain English'})])
    answer = asyncio.run(ApplicationService(FunctionModel(respond), routine_tools=True).run(
        OwnerTurn('owner', 'owner', 'save', 'Remember plain English')))
    assert not SavedPreference.objects.exists() and 'Nothing changed' in answer


@pytest.mark.django_db(transaction=True)
def test_mixed_inspection_returns_case_and_preferences_without_model_receiving_case_values() -> None:
    case = GrievanceCase.objects.create(owner_id='owner', label='Shop', label_key='shop', revision=1)
    CaseFactRevision.objects.create(case=case, revision=1, facts={'purchase_date': '21 November'})
    CaseSelection.objects.create(owner_id='owner', conversation_id='owner', case=case)
    SavedPreference.objects.create(identity='owner', key='reply_style', value='plain English', source_message_id='source')
    calls = []
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        assert '21 November' not in str(messages)
        return ModelResponse(parts=[ToolCallPart('inspect_case', {'fields': ['purchase_date'], 'include_preferences': True})])
    answer = asyncio.run(ApplicationService(FunctionModel(respond), routine_tools=True).run(
        OwnerTurn('owner', 'owner', 'inspect', 'What purchase date is recorded and what reply style have I saved?')))
    assert '21 November' in answer and 'plain English' in answer and len(calls) == 1
    case.refresh_from_db()
    assert case.revision == 1


@pytest.mark.django_db(transaction=True)
def test_out_of_scope_delegates_without_another_router_call() -> None:
    calls = []
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[ToolCallPart('delegate', {'specialist': 'research'})])
    async def research(message: str) -> str:
        return 'Evidence with citation.'
    answer = asyncio.run(ApplicationService(FunctionModel(respond), research, routine_tools=True).run(
        OwnerTurn('owner', 'owner', 'research', 'Find official Python documentation.')))
    assert answer == 'Evidence with citation.' and len(calls) == 1


@pytest.mark.django_db(transaction=True)
def test_inspection_without_selected_case_asks_instead_of_reading_another_owner() -> None:
    other = GrievanceCase.objects.create(owner_id='other', label='Shop', label_key='shop', revision=1)
    CaseFactRevision.objects.create(case=other, revision=1, facts={'purchase_date': 'secret date'})
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart('inspect_case', {'fields': ['purchase_date']})])
    answer = asyncio.run(ApplicationService(FunctionModel(respond), routine_tools=True).run(
        OwnerTurn('owner', 'owner', 'inspect', 'What date did I purchase it?')))
    assert answer == 'Which case would you like me to check?'


@pytest.mark.django_db(transaction=True)
def test_experiment_rejects_unnecessary_clarification_for_temporary_request() -> None:
    case = next(case for case in CASES if case.name == 'temporary')
    asyncio.run(RoutineExperiment.seed('evaluation-owner', case))
    checks = asyncio.run(RoutineExperiment.checks('evaluation-owner', case,
        'Should this be a lasting saved preference, or only for this task?'))
    assert not checks['temporary_request_understood']


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('enabled', [True, False])
def test_subscription_runtime_respects_routine_rollout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    calls = []
    request = 'Remember that I prefer plain English.'
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        if not enabled and len(calls) == 1:
            return ModelResponse(parts=[ToolCallPart('final_result', {'specialist': 'preferences'})])
        return ModelResponse(parts=[ToolCallPart('manage_preference' if enabled else 'final_result', {
            'action': 'save', 'key': 'reply_style', 'value': 'plain English',
            'value_quote': 'plain English', 'consent_quote': request})])

    @asynccontextmanager
    async def model(self: SubscriptionService, name: str = '') -> AsyncIterator[Model]:
        yield FunctionModel(respond)

    async def public(message: str) -> str:
        raise AssertionError('Preference requests must not use web research')

    monkeypatch.setattr(settings, 'ROUTINE_TOOLS_ENABLED', enabled)
    monkeypatch.setattr(SubscriptionService, 'model', model)
    service = SubscriptionApplicationService(SubscriptionService(tmp_path), public)
    answer = asyncio.run(service.run(OwnerTurn('owner', 'owner', 'save', request)))
    assert 'Saved preference' in answer
    assert SavedPreference.objects.get(identity='owner').value == 'plain English'
    assert len(calls) == (1 if enabled else 2)

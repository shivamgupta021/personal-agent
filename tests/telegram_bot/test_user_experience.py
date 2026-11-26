"""Ordinary requests should not expose workflow internals or repeat classification."""
import asyncio
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.capability_constants import CAPABILITY_DESCRIPTION
from core.turns import OwnerTurn
from core.service import ApplicationService
from preferences.models import SavedPreference
from tests.telegram_bot.test_gmail import DraftHarness
from grievances.models import GrievanceCase, CaseFactRevision


def test_intro_is_short_and_actionable() -> None:
    assert len(CAPABILITY_DESCRIPTION.split()) <= 40
    assert 'tell me' in CAPABILITY_DESCRIPTION.casefold()


@pytest.mark.django_db(transaction=True)
def test_explicit_preference_uses_router_and_one_specialist_call() -> None:
    calls = []
    request = 'Remember that I prefer short, plain-English replies.'

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(info)
        if len(calls) == 1:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist':'preferences'})])
        assert len(calls) == 2, 'Unexpected additional model call'
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {

                'action': 'save', 'key': 'reply_style', 'value': 'short, plain-English replies',
                'value_quote': 'short, plain-English replies', 'consent_quote': request,
        })])

    answer = asyncio.run(ApplicationService(FunctionModel(respond)).run(OwnerTurn('owner', 'owner', 'pref', request)))
    assert 'Saved' in answer
    assert SavedPreference.objects.get(identity='owner').value == 'short, plain-English replies'
    assert len(calls) == 2


@pytest.mark.django_db(transaction=True)
def test_preference_proposal_cannot_forge_user_permission() -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if 'specialist' in info.output_tools[0].parameters_json_schema.get('properties', {}):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist':'preferences'})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {

                'action': 'save', 'key': 'reply_style', 'value': 'short',
                'value_quote': 'short', 'consent_quote': 'Remember this',
        })])
    answer = asyncio.run(ApplicationService(FunctionModel(respond)).run(
        OwnerTurn('owner', 'owner', 'forged', 'Explain what a preference is.')))
    assert 'Nothing changed' in answer
    assert not SavedPreference.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_requesting_full_refund_is_a_goal_change_not_historical_fact_correction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = DraftHarness(tmp_path)
    original = h.model
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        properties = info.output_tools[0].parameters_json_schema.get('properties', {}) if info.output_tools else {}
        if h.text.startswith('Ask') and 'action' in properties:
            payload = json.loads(str(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart))))
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'action': 'case_help', 'case_id': payload['selected_case']['id'],
                'intent_quote': h.text, 'gmail_draft': True,
                'facts': [{'key': 'requested_remedy', 'value': 'full refund', 'quote': 'full refund'}],
            })])
        return original(messages, info)
    async def scenario() -> None:
        await h.send('I agree to email processing')
        await h.send('Help me get my Kettle refund')
        case = await GrievanceCase.objects.aget(owner_id='test:7')
        revision = await CaseFactRevision.objects.aget(case=case, revision=case.revision)
        revision.facts['requested_remedy'] = 'refund'
        await revision.asave()
        monkeypatch.setattr(h, 'model', respond)
        answer = await h.send('Ask them for the full refund. Keep it short.')
        assert 'ready for your review' in answer
        assert len(h.created) == 1
    try:
        asyncio.run(scenario())
    finally:
        h.http.close()

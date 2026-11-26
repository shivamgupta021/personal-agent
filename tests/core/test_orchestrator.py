"""Exercise delegation through the real application turn, not just router schema."""
import asyncio
import json
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.service import ApplicationService
from core.turns import OwnerTurn
from grievances.models import GrievanceCase, CaseFactRevision, CaseSelection
from preferences.models import SavedPreference


@pytest.mark.django_db(transaction=True)
def test_mixed_case_and_conversation_question_returns_both_without_leaking_case_facts() -> None:
    stages: list[str] = []
    case = GrievanceCase.objects.create(owner_id='owner', label='Shop', label_key='shop', revision=1)
    CaseFactRevision.objects.create(case=case, revision=1, facts={'purchase_date': '21 November', 'order_reference': 'PRIVATE-ORDER'})
    CaseSelection.objects.create(owner_id='owner', conversation_id='owner', case=case)
    question = 'which two languages did I mention earlier?'
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result: dict[str, Any]
        if not info.output_tools:
            stages.append('conversation')
            assert {tool.name for tool in info.function_tools} == {'recall_conversation'}
            assert 'PRIVATE-ORDER' not in str(messages)
            assert 'purchase date' not in str(messages)
            return ModelResponse(parts=[TextPart('Python and Ruby.')])
        schema = info.output_tools[0].parameters_json_schema
        if 'specialist' in schema['properties']:
            stages.append('route')
            result = {'specialist': 'grievance', 'conversation_question': question}
        else:
            stages.append('case')
            result = {'action': 'show', 'requested_fields': ['purchase_date']}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])
    answer = asyncio.run(ApplicationService(FunctionModel(respond)).run(OwnerTurn(
        'owner', 'owner', 'mixed', 'What purchase date did I correct, and ' + question)))
    assert '21 November' in answer and 'Python and Ruby.' in answer
    assert stages == ['route', 'case', 'conversation']


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('question', ['Invented question', 'What is PRIVATE-ORDER?'])
def test_mixed_route_cannot_invent_or_move_case_details_into_conversation(question: str) -> None:
    case = GrievanceCase.objects.create(owner_id='owner', label='Shop', label_key='shop', revision=1)
    CaseFactRevision.objects.create(case=case, revision=1, facts={'order_reference': 'PRIVATE-ORDER'})
    CaseSelection.objects.create(owner_id='owner', conversation_id='owner', case=case)
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert info.output_tools, 'Must not invoke the conversation specialist'
        schema = info.output_tools[0].parameters_json_schema
        result = ({'specialist': 'grievance', 'conversation_question': question}
                  if 'specialist' in schema['properties'] else {'action': 'show'})
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])
    answer = asyncio.run(ApplicationService(FunctionModel(respond)).run(OwnerTurn(
        'owner', 'owner', 'mixed', 'Show my case. What is PRIVATE-ORDER?')))
    assert 'Please ask the unrelated conversation question separately.' in answer


@pytest.mark.django_db(transaction=True)
def test_main_only_routes_and_preference_specialist_saves_without_final_generation() -> None:
    stages: list[str] = []
    request = 'Remember that I prefer plain English.'
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result: dict[str, Any]
        schema = info.output_tools[0].parameters_json_schema
        if not stages:
            assert set(schema['properties']) == {'specialist', 'conversation_question'}
            assert 'facts' not in schema['properties']
            stages.append('orchestrator')
            result = {'specialist': 'preferences'}
        else:
            assert stages == ['orchestrator'], 'Unexpected extra inference'
            assert 'consent_quote' in schema['properties']
            stages.append('preferences')
            result = {'action': 'save', 'key': 'reply_style', 'value': 'plain English',
                      'value_quote': 'plain English', 'consent_quote': request}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])
    answer = asyncio.run(ApplicationService(FunctionModel(respond)).run(OwnerTurn('owner','owner','save',request)))
    assert answer == 'Saved preference: reply style: plain English.'
    assert SavedPreference.objects.get(identity='owner').value == 'plain English'
    assert stages == ['orchestrator','preferences']


@pytest.mark.django_db(transaction=True)
def test_case_specialist_alone_receives_facts_and_extracts_update_once() -> None:
    stages: list[str] = []
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result: dict[str, Any]
        schema = info.output_tools[0].parameters_json_schema
        payload = json.loads(str(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart))))
        if 'specialist' in schema['properties']:
            assert 'PRIVATE-ORDER' not in str(payload)
            stages.append('orchestrator')
            result = {'specialist':'grievance'}
        else:
            stages.append('grievance')
            assert payload['selected_case']['facts']['order_reference'] == 'PRIVATE-ORDER'
            result = {'action':'update','facts':[{'key':'purchase_date','value':'20 November','quote':'20 November'}],
                      'correction_quote':'Change the purchase date to 20 November'}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,result)])
    from_context = {'order_reference':'PRIVATE-ORDER','purchase_date':'19 November'}
    case = GrievanceCase.objects.create(owner_id='owner',label='Shop',label_key='shop',revision=1)
    CaseFactRevision.objects.create(case=case,revision=1,facts=from_context)
    CaseSelection.objects.create(owner_id='owner',conversation_id='owner',case=case)
    answer = asyncio.run(ApplicationService(FunctionModel(respond)).run(
        OwnerTurn('owner','owner','update','Change the purchase date to 20 November')))
    assert answer == 'Updated purchase date: **20 November**.'
    assert stages == ['orchestrator','grievance']


@pytest.mark.django_db(transaction=True)
def test_conversation_specialist_answers_and_main_does_not_rewrite() -> None:
    calls: list[str] = []
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            assert not calls
            calls.append('route')
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist':'conversation'})])
        calls.append('conversation')
        assert calls == ['route','conversation']
        return ModelResponse(parts=[TextPart('Python and Ruby.')])
    assert asyncio.run(ApplicationService(FunctionModel(respond)).run(
        OwnerTurn('owner','owner','chat','My languages are Python and Ruby. Acknowledge.'))) == 'Python and Ruby.'
    assert calls == ['route','conversation']


@pytest.mark.django_db(transaction=True)
def test_research_receives_original_request_without_case_payload() -> None:
    received = []
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist':'research'})])
    async def research(message: str) -> str:
        received.append(message)
        return 'Cited research result.'
    answer = asyncio.run(ApplicationService(FunctionModel(respond),research).run(
        OwnerTurn('owner','owner','web','Find official Python docs.')))
    assert answer == 'Cited research result.'
    assert received == ['Find official Python docs.']


@pytest.mark.django_db(transaction=True)
def test_router_receives_bounded_dialogue_for_followup_despite_selected_case() -> None:
    stages: list[str] = []
    case = GrievanceCase.objects.create(owner_id='owner', label='Shop', label_key='shop', revision=1)
    CaseFactRevision.objects.create(case=case, revision=1, facts={'order_reference':'PRIVATE-ORDER'})
    CaseSelection.objects.create(owner_id='owner', conversation_id='owner', case=case)
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            payload = json.loads(str(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart))))
            assert payload['has_selected_case']
            assert 'PRIVATE-ORDER' not in str(payload)
            if stages:
                assert 'Python and Ruby' in str(payload['recent_conversation'])
            stages.append('route')
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist':'conversation'})])
        stages.append('conversation')
        return ModelResponse(parts=[TextPart('Python and Ruby.')])
    async def run() -> None:
        for delivery, message in [('one','For this comparison, my languages are Python and Ruby.'),('two','Which two languages did I mention?')]:
            assert await ApplicationService(FunctionModel(respond)).run(OwnerTurn('owner','owner',delivery,message)) == 'Python and Ruby.'
    asyncio.run(run())
    assert stages == ['route','conversation','route','conversation']

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from django.test import override_settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from conversations.memory import ConversationMemoryRunner
from conversations.service import ConversationService
from core.tracing import tracing
from core.turns import OwnerTurn
from core.service import ApplicationService
from grievances.service import CaseService
from core.runtime import SubscriptionApplicationService
from preferences.store import PreferenceStore
from subscriptions.service import SubscriptionService


@pytest.mark.django_db(transaction=True)
def test_selected_case_preserves_private_preference_management_and_forgetting() -> None:
    current_request = ''
    after_forgetting = False
    secret = 'ULTRABRIEF-PRIVATE-PREF'
    seen_tools: list[str] = []
    exporter = InMemorySpanExporter()
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        data = json.loads(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart) and isinstance(p.content, str))) if 'You interpret grievance requests' in str(info) or 'PREFERENCE_AUTHORIZATION' in str(info) or 'FACT_EDIT_REDACTION' in str(info) else {}
        proposal: dict[str, Any]
        if info.output_tools and 'specialist' in info.output_tools[0].parameters_json_schema.get('properties', {}):
            proposal = {'specialist': 'grievance' if current_request.startswith(('Create', 'Show')) else 'conversation'}
        elif 'You interpret grievance requests' in str(info):
            if after_forgetting:
                assert secret not in str(messages)
            if current_request.startswith('Create'):
                proposal = {'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case',
                            'facts': [{'key': 'order_reference', 'value': 'PRIVATE-ORDER', 'quote': 'PRIVATE-ORDER'}]}
            elif current_request.startswith('Show'):
                proposal = {'action': 'show'}
            else:
                proposal = {'action': 'memory'}
        elif 'PREFERENCE_AUTHORIZATION' in str(info):
            action = 'forget' if current_request.startswith('Forget') else 'list' if current_request.startswith('List') else 'save'
            proposal = {'action': action, 'key': 'reply_style', 'value': secret,
                        'consent_quote': current_request, 'value_quote': secret}
        elif 'FACT_EDIT_REDACTION' in str(info):
            proposal = {'edits': [{'segment_id': segment['id'], 'quote': segment['text']} for segment in data['segments']]}
        elif 'FACT_EDIT_VERIFICATION' in str(info):
            proposal = {'safe': True}
        else:
            assert 'PRIVATE-ORDER' not in str(messages)
            seen_tools.extend(tool.name for tool in info.function_tools)
            return ModelResponse(parts=[ToolCallPart('manage_preferences', {})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, proposal)])
    async def run() -> None:
        nonlocal current_request, after_forgetting
        service = ApplicationService(FunctionModel(respond))
        requests = ['Create a case called Kettle. PRIVATE-ORDER', f'Remember that I prefer {secret} answers.',
                    'List my saved preferences.', 'Forget my saved reply style.', 'Show this case.']
        for index, request in enumerate(requests):
            current_request = request
            answer = await service.run(OwnerTurn('owner', 'owner', str(index), request))
            if index == 1:
                assert secret in answer
                assert (await PreferenceStore().list('owner'))[0].value == secret
            if index == 3:
                assert 'forgotten' in answer.lower()
                assert await PreferenceStore().list('owner') == []
                after_forgetting = True
        case = (await service.cases.store.list_cases('owner'))[0]
        assert (await service.cases.store.selection('owner', 'owner')).case_id == case.id
        view = await service.cases.store.get_case('owner', case.id)
        assert view and view.facts == {'order_reference': 'PRIVATE-ORDER'} and secret not in str(view)
        assert seen_tools and 'search' not in seen_tools and 'read_page' not in seen_tools
    tracing.shutdown()
    try:
        with override_settings(LANGFUSE_CAPTURE_CONTENT=True):
            tracing.configure(exporter)
            asyncio.run(run())
            tracing.flush()
            output = '\n'.join(span.to_json() for span in exporter.get_finished_spans())
            assert secret not in output and 'PRIVATE-ORDER' not in output
    finally:
        tracing.shutdown()


@pytest.mark.django_db(transaction=True)
def test_exact_conversation_deletion_works_without_model_credentials_and_preserves_case(tmp_path: Path) -> None:
    def create(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
            {'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case'})])
    def unavailable(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError('Deletion cannot call a model')
    async def run() -> None:
        service = CaseService(FunctionModel(create))
        await service.run(OwnerTurn('owner', 'owner', '1', 'Create a case called Kettle'))
        case = (await service.store.list_cases('owner'))[0]
        memory = ConversationService('owner', ConversationMemoryRunner(FunctionModel(unavailable)))
        runtime = SubscriptionApplicationService(SubscriptionService(tmp_path), memory.run)
        reply = await runtime.run(OwnerTurn('owner', 'owner', '2', 'Delete this entire conversation.'))
        assert 'deleted locally' in reply.lower()
        assert await service.store.get_case('owner', case.id)
        assert (await service.store.selection('owner', 'owner')).case_id == case.id
    asyncio.run(run())

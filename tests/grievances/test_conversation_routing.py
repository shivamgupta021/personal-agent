"""Conversation regressions through the same application boundary used by Telegram."""

import asyncio

import pytest
from django.test import override_settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn
from core.tracing import tracing
from grievances.service import ApplicationService


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('selected', [False, True])
def test_ordinary_conversation_survives_restart_without_case_or_web_access(selected: bool) -> None:
    request = ''
    conversation_calls = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal conversation_calls
        if info.output_tools:
            if request.startswith('Create'):
                proposal = {'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case',
                            'facts': [{'key': 'order_reference', 'value': 'PRIVATE-ORDER', 'quote': 'PRIVATE-ORDER'}]}
            else:
                proposal = {'action': 'conversation'}
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, proposal)])
        conversation_calls += 1
        history = str(messages)
        assert 'PRIVATE-ORDER' not in history
        tools = {tool.name for tool in info.function_tools}
        assert 'search' not in tools and 'read_page' not in tools
        assert 'recall_conversation' in tools
        assert 'Python and Ruby' in history
        return ModelResponse(parts=[TextPart('Your two languages are Python and Ruby.')])

    async def public(message: str) -> str:
        raise AssertionError('A no-search conversation must not enter the web-enabled researcher')

    async def run() -> None:
        nonlocal request
        model = FunctionModel(respond)
        app = ApplicationService(model, public)
        if selected:
            request = 'Create a case called Kettle. PRIVATE-ORDER'
            await app.run(OwnerTurn('owner', 'owner', 'create', request))
        before = await app.store.selection('owner', 'owner')
        for index, text in enumerate((
            'For this fictional comparison, my two languages are Python and Ruby. Acknowledge without searching.',
            'Which two languages did I mention?',
        )):
            request = text
            app = ApplicationService(model, public)
            answer = await app.run(OwnerTurn('owner', 'owner', str(index), text))
            assert 'Python and Ruby' in answer
            assert 'select a case' not in answer
        after = await app.store.selection('owner', 'owner')
        assert after.case_id == before.case_id
        assert len(await app.store.list_cases('owner')) == int(selected)
        assert conversation_calls == 2
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_application_trace_groups_route_and_conversation_without_private_content() -> None:
    exporter = InMemorySpanExporter()
    secret = 'SYNTHETIC-CONVERSATION-MARKER'

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'action': 'conversation'})])
        return ModelResponse(parts=[TextPart(secret)])

    tracing.shutdown()
    try:
        with override_settings(LANGFUSE_CAPTURE_CONTENT=True):
            tracing.configure(exporter)
            app = ApplicationService(FunctionModel(respond))
            answer = asyncio.run(app.run(OwnerTurn('owner', 'owner', 'one', secret)))
            assert answer == secret
            tracing.flush()
            spans = exporter.get_finished_spans()
            roots = [span for span in spans if span.parent is None]
            assert len(roots) == 1 and roots[0].name == 'application.turn'
            assert len({span.context.trace_id for span in spans if span.context}) == 1
            assert any(span.attributes and span.attributes.get('application.route') == 'conversation' for span in spans)
            assert secret not in '\n'.join(span.to_json() for span in spans)
    finally:
        tracing.shutdown()

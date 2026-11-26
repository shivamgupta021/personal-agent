"""Conversation regressions through the same application boundary used by Telegram."""

import asyncio
import json

import pytest
from django.test import override_settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn
from core.tracing import tracing
from core.service import ApplicationService


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('selected', [False, True])
def test_ordinary_conversation_survives_restart_without_case_or_web_access(selected: bool) -> None:
    request = ''
    conversation_calls = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal conversation_calls
        if info.output_tools:
            if 'specialist' in info.output_tools[0].parameters_json_schema.get('properties', {}):
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist': 'grievance' if request.startswith('Create') else 'conversation'})])
            if request.startswith('Create'):
                proposal = {'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case',
                            'facts': [{'key': 'order_reference', 'value': 'PRIVATE-ORDER', 'quote': 'PRIVATE-ORDER'}]}
            else:
                proposal = {'specialist': 'conversation'}
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
        before = await app.cases.store.selection('owner', 'owner')
        for index, text in enumerate((
            'For this fictional comparison, my two languages are Python and Ruby. Acknowledge without searching.',
            'Which two languages did I mention?',
        )):
            request = text
            app = ApplicationService(model, public)
            answer = await app.run(OwnerTurn('owner', 'owner', str(index), text))
            assert 'Python and Ruby' in answer
            assert 'select a case' not in answer
        after = await app.cases.store.selection('owner', 'owner')
        assert after.case_id == before.case_id
        assert len(await app.cases.store.list_cases('owner')) == int(selected)
        assert conversation_calls == 2
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('capture,private_capture', [(False, False), (False, True), (True, False), (True, True)])
def test_application_trace_content_respects_both_capture_settings(capture: bool, private_capture: bool) -> None:
    exporter = InMemorySpanExporter()
    secret = 'SYNTHETIC-CONVERSATION-MARKER'

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist': 'conversation'})])
        return ModelResponse(parts=[TextPart(secret)])

    tracing.shutdown()
    try:
        with override_settings(LANGFUSE_CAPTURE_CONTENT=capture, LANGFUSE_CAPTURE_PRIVATE_CONTENT=private_capture):
            tracing.configure(exporter)
            app = ApplicationService(FunctionModel(respond))
            reply = asyncio.run(app.prepare(OwnerTurn('owner', 'owner', 'one', secret)))
            assert asyncio.run(reply.render()) == secret
            tracing.flush()
            spans = exporter.get_finished_spans()
            roots = [span for span in spans if span.parent is None]
            assert len(roots) == 1 and roots[0].name == 'application.turn'
            assert roots[0].context is not None
            assert reply.trace_id == format(roots[0].context.trace_id, '032x')
            assert len({span.context.trace_id for span in spans if span.context}) == 1
            assert any(span.attributes and span.attributes.get('orchestrator.specialist') == 'conversation' for span in spans)
            assert (secret in '\n'.join(span.to_json() for span in spans)) == (capture and private_capture)
            if capture and private_capture:
                attributes = roots[0].attributes or {}
                input_value = attributes['langfuse.observation.input']
                output_value = attributes['langfuse.observation.output']
                assert isinstance(input_value, str) and isinstance(output_value, str)
                assert json.loads(input_value) == {'message': secret}
                assert json.loads(output_value) == {'answer': secret}
    finally:
        tracing.shutdown()

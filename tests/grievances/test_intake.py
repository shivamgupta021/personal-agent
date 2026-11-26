import asyncio
import json

import pytest
from django.test import override_settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from core.tracing import tracing
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn
from grievances.service import ApplicationService


@pytest.mark.django_db(transaction=True)
def test_create_retries_model_invented_label_before_asking_owner() -> None:
    calls = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'action': 'create',
            'label': 'Attachment QA Store (fictional)' if calls == 1 else 'Attachment QA Store',
            'intent_quote': 'Create a fictional refund case for Attachment QA Store',
        })])

    async def scenario() -> None:
        service = ApplicationService(FunctionModel(respond))
        answer = await service.run(OwnerTurn('owner', 'chat', 'create',
            'Create a fictional refund case for Attachment QA Store, separate from my real purchases.'))
        assert 'saved and selected' in answer
        assert [case.label for case in await service.store.list_cases('owner')] == ['Attachment QA Store']

    asyncio.run(scenario())
    assert calls == 2


@pytest.mark.django_db(transaction=True)
def test_create_missing_facts_restart_and_replay() -> None:
    calls = 0
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'action': 'create', 'label': 'Lumen kettle', 'intent_quote': 'Create a refund case',
            'facts': [{'key': 'company', 'value': 'Lumen', 'quote': 'Lumen'},
                      {'key': 'issue', 'value': 'broken', 'quote': 'broken'},
                      {'key': 'requested_remedy', 'value': 'refund', 'quote': 'refund'}],
        })])
    async def run() -> None:
        turn = OwnerTurn('owner-a', 'chat-a', 'telegram:1', 'Create a refund case called Lumen kettle. Lumen kettle is broken.')
        service = ApplicationService(FunctionModel(respond))
        reply = await service.run(turn)
        assert 'saved' in reply.lower()
        assert 'amount' in reply.lower()
        cases = await service.store.list_cases('owner-a')
        assert len(cases) == 1
        case = await service.store.get_case('owner-a', cases[0].id)
        assert case and case.status == 'open' and case.facts == {'company': 'Lumen', 'issue': 'broken', 'requested_remedy': 'refund'}
        restarted = ApplicationService(FunctionModel(respond))
        assert (await restarted.store.selection('owner-a', 'chat-a')).case_id == case.id
        assert 'already' in (await restarted.run(turn)).lower()
        assert len(await restarted.store.list_cases('owner-a')) == 1
        assert await restarted.store.list_cases('owner-b') == []
    asyncio.run(run())
    assert calls == 1


@pytest.mark.django_db(transaction=True)
def test_conflicting_amount_requires_explicit_correction_and_preserves_history() -> None:
    proposed: dict[str, object] = {}
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, proposed)])
    async def run() -> None:
        service = ApplicationService(FunctionModel(respond))
        proposed.update(action='create', label='Kettle', intent_quote='Create a case',
                        facts=[{'key': 'amount_paid', 'value': '2,400', 'quote': '2,400'}])
        await service.run(OwnerTurn('a', 'a', '1', 'Create a case called Kettle. Paid 2,400.'))
        case_id = (await service.store.list_cases('a'))[0].id
        proposed.clear()
        proposed.update(action='update', facts=[{'key': 'amount_paid', 'value': '2,700', 'quote': '2,700'}],
                        correction_quote='The amount was 2,700')
        reply = await service.run(OwnerTurn('a', 'a', '2', 'The amount was 2,700'))
        assert 'correction' in reply.lower()
        current = await service.store.get_case('a', case_id)
        assert current and current.facts['amount_paid'] == '2,400' and current.revision == 1
        proposed['correction_quote'] = 'Correction: the amount was 2,700'
        turn = OwnerTurn('a', 'a', '3', 'Correction: the amount was 2,700')
        assert await service.run(turn) == 'Updated amount paid: **2,700**.'
        await ApplicationService(FunctionModel(respond)).run(turn)
        current = await service.store.get_case('a', case_id)
        assert current and current.facts['amount_paid'] == '2,700' and current.revision == 2
        assert current.history == [{'amount_paid': '2,400'}, {'amount_paid': '2,700'}]
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_owner_selection_isolation_ambiguity_and_return_to_public_research() -> None:
    proposal: dict[str, object] = {}
    inputs: list[str] = []
    public: list[str] = []
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        inputs.append(str(messages))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, proposal)])
    async def research(message: str) -> str:
        public.append(message)
        return 'Public answer'
    async def run() -> None:
        service = ApplicationService(FunctionModel(respond), research)
        for owner, delivery, label, secret in [('a', '1', 'Kettle', 'SECRET-A'), ('a', '2', 'Headphones', 'SECRET-B'), ('b', '1', 'Tablet', 'SECRET-C')]:
            proposal.clear()
            proposal.update(action='create', label=label, intent_quote='Create a case',
                            facts=[{'key': 'order_reference', 'value': secret, 'quote': secret}])
            inputs.clear()
            await service.run(OwnerTurn(owner, owner, delivery, f'Create a case called {label}. Order {secret}.'))
            assert all(other not in str(inputs) for other in ['SECRET-A', 'SECRET-B', 'SECRET-C'] if other != secret)
        cases = await service.store.list_cases('a')
        assert len(cases) == 2
        hidden = (await service.store.list_cases('b'))[0]
        proposal.clear()
        proposal.update(action='select', case_id=hidden.id, intent_quote='Select Tablet')
        assert 'unavailable' in (await service.run(OwnerTurn('a', 'a', '3', 'Select Tablet'))).lower()
        assert await service.store.get_case('a', hidden.id) is None
        proposal.update(case_id=cases[0].id, intent_quote='Select Kettle')
        reply = await service.run(OwnerTurn('a', 'a', '4', 'Select Kettle'))
        assert 'SECRET-A' in reply and 'SECRET-B' not in reply and 'SECRET-C' not in reply
        proposal.clear()
        proposal.update(action='leave', intent_quote='Return to general research')
        await service.run(OwnerTurn('a', 'a', '5', 'Return to general research'))
        before = await service.store.get_case('a', cases[0].id)
        proposal.clear()
        proposal.update(action='clarify')
        await service.run(OwnerTurn('a', 'a', '6', 'Add broken to that order'))
        assert await service.store.get_case('a', cases[0].id) == before
        assert not public
        proposal.update(action='public')
        inputs.clear()
        assert await service.run(OwnerTurn('a', 'a', '7', 'Find official Python docs')) == 'Public answer'
        assert public == ['Find official Python docs']
        assert 'SECRET-' not in str(inputs)
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', ['none', 'validation', 'provider'])
def test_private_trace_payload_exclusion_with_global_content_capture(failure: str) -> None:
    secret = 'PRIVATE-CORRESPONDENCE-ALPHA'
    exporter = InMemorySpanExporter()
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if failure == 'provider':
            raise RuntimeError(secret)
        args = {'action': 'update', 'facts': [{'key': 'correspondence', 'value': secret, 'quote': secret}]} if failure == 'none' else {'action': secret}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])
    async def run() -> None:
        service = ApplicationService(FunctionModel(respond))
        if failure == 'none':
            answer = await service.run(OwnerTurn('private-owner', 'private-chat', '1', secret))
            assert secret not in answer
        else:
            with pytest.raises(Exception):
                await service.run(OwnerTurn('private-owner', 'private-chat', '1', secret))
        assert await service.store.list_cases('private-owner') == []
    tracing.shutdown()
    try:
        with override_settings(LANGFUSE_CAPTURE_CONTENT=True):
            tracing.configure(exporter)
            asyncio.run(run())
            assert tracing.flush()
            spans = exporter.get_finished_spans()
            assert spans
            serialized = '\n'.join(span.to_json() for span in spans)
            assert secret not in serialized
            assert 'application.route' in serialized
    finally:
        tracing.shutdown()


@pytest.mark.django_db(transaction=True)
def test_manual_resend_with_new_delivery_reuses_matching_case() -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case',
            'facts': [{'key': 'issue', 'value': 'broken', 'quote': 'broken'}],
        })])
    async def run() -> None:
        service = ApplicationService(FunctionModel(respond))
        for delivery in ['first', 'manual-resend']:
            await service.run(OwnerTurn('owner', 'chat', delivery, 'Create a case called Kettle. It is broken.'))
        cases = await service.store.list_cases('owner')
        assert len(cases) == 1
        case = await service.store.get_case('owner', cases[0].id)
        assert case and case.revision == 1
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_public_misroute_cannot_export_known_private_case_fields() -> None:
    proposal: dict[str, object] = {'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case',
        'facts': [{'key': 'order_reference', 'value': 'ORDER-PRIVATE-Q7', 'quote': 'ORDER-PRIVATE-Q7'}]}
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, proposal)])
    public: list[str] = []
    async def research(message: str) -> str:
        public.append(message)
        return 'Research called'
    async def run() -> None:
        service = ApplicationService(FunctionModel(respond), research)
        await service.run(OwnerTurn('owner', 'chat', '1', 'Create a case called Kettle. ORDER-PRIVATE-Q7'))
        proposal.clear()
        proposal.update(action='leave', intent_quote='Return to general research')
        await service.run(OwnerTurn('owner', 'chat', '2', 'Return to general research'))
        proposal.clear()
        proposal.update(action='public')
        await service.run(OwnerTurn('owner', 'chat', '3', 'Search ORDER-PRIVATE-Q7'))
        assert public == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_selected_context_clarifies_conflicting_company_before_any_write() -> None:
    stage = 'create'
    selected_seen = False
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal selected_seen
        if stage == 'create':
            proposal: dict[str, object] = {'action': 'create', 'label': 'Birthday gift', 'intent_quote': 'Create a case',
                'facts': [{'key': 'company', 'value': 'Lumen Home', 'quote': 'Lumen Home'},
                          {'key': 'order_reference', 'value': 'ORDER-ALPHA-Q7', 'quote': 'ORDER-ALPHA-Q7'}]}
        elif 'This is the selected-case intake stage' in str(info):
            selected_seen = True
            assert 'Lumen Home' in str(messages) and 'ORDER-ALPHA-Q7' in str(messages)
            proposal = {'action': 'clarify'}
        else:
            assert 'ORDER-ALPHA-Q7' not in str(messages)
            proposal = {'action': 'update', 'facts': [{'key': 'order_reference', 'value': 'ORDER-BETA-R9', 'quote': 'ORDER-BETA-R9'}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, proposal)])
    async def run() -> None:
        nonlocal stage
        service = ApplicationService(FunctionModel(respond))
        await service.run(OwnerTurn('owner', 'owner', '1', 'Create a case called Birthday gift. Company Lumen Home. ORDER-ALPHA-Q7'))
        case = (await service.store.list_cases('owner'))[0]
        before = await service.store.get_case('owner', case.id)
        stage = 'update'
        reply = await service.run(OwnerTurn('owner', 'owner', '2', 'For Cobalt Audio, update the order reference to ORDER-BETA-R9.'))
        assert selected_seen and 'clarify' in reply.lower()
        assert await service.store.get_case('owner', case.id) == before
    asyncio.run(run())

@pytest.mark.django_db(transaction=True)
def test_independent_public_question_preserves_selected_case_without_exporting_it() -> None:
    proposal: dict[str, object] = {'action': 'create', 'label': 'Kettle', 'intent_quote': 'Create a case',
        'facts': [{'key': 'order_reference', 'value': 'PRIVATE-ORDER', 'quote': 'PRIVATE-ORDER'}]}
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, proposal)])
    public: list[str] = []
    async def research(message: str) -> str:
        public.append(message)
        return 'Official Python documentation'
    async def run() -> None:
        app = ApplicationService(FunctionModel(respond), research)
        await app.run(OwnerTurn('owner', 'chat', 'create', 'Create a case called Kettle. PRIVATE-ORDER'))
        selected = await app.store.selection('owner', 'chat')
        proposal.clear()
        proposal['action'] = 'public'
        assert await app.run(OwnerTurn('owner', 'chat', 'public', 'Find official Python documentation')) == 'Official Python documentation'
        assert public == ['Find official Python documentation']
        assert await app.store.selection('owner', 'chat') == selected
        await app.run(OwnerTurn('owner', 'chat', 'private', 'Search for PRIVATE-ORDER'))
        assert len(public) == 1
    asyncio.run(run())

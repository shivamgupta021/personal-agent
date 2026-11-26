import asyncio
from collections.abc import Awaitable, Callable

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded, UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.tracing import tracing
from core.turns import OwnerTurn
from grievances.models import GrievanceCase
from grievances.service import CaseService
from grievances.store import CaseStore


FACTS = {'company': 'Lumen Home', 'purchase_context': 'kettle', 'issue': 'did not turn on',
         'requested_remedy': 'refund', 'purchase_date': '2026-11-02', 'amount_paid': 'INR 2,400',
         'refund_amount': 'INR 2,400', 'order_reference': 'ORDER-ALPHA-Q7',
         'evidence': 'I may have a receipt; I have not checked. I do not have a video.'}


class DraftConversation:
    def __init__(self) -> None:
        self.action: dict[str, object] = {}
        self.plan: dict[str, object] = {'fact_order': ['issue', 'purchase_context'], 'greeting': 'dear_support'}
        self.inputs: list[str] = []
        self.generation: Callable[[], Awaitable[None]] | None = None
        self.service = CaseService(FunctionModel(self.respond))
        self.delivery = 0

    async def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.inputs.append(str(messages))
        is_plan = 'fact_order' in info.output_tools[0].parameters_json_schema.get('properties', {})
        if is_plan and self.generation:
            await self.generation()
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, self.plan if is_plan else self.action)])

    async def turn(self, text: str, action: str, *, owner: str = 'alpha', delivery: str | None = None, **fields: object) -> str:
        self.delivery += 1
        self.action = {'action': action, **fields}
        return await self.service.run(OwnerTurn(owner, owner, delivery or str(self.delivery), text))

    async def create(self, *, owner: str = 'alpha', facts: dict[str, str] | None = None, label: str = 'Lumen kettle') -> str:
        supplied = FACTS if facts is None else facts
        text = f'Create a case called {label}. ' + '\n'.join(supplied.values())
        await self.turn(text, 'create', owner=owner, label=label, intent_quote='Create a case',
                        facts=[{'key': key, 'value': value, 'quote': value} for key, value in supplied.items()])
        return (await self.service.store.selection(owner, owner)).case_id or ''


@pytest.mark.django_db(transaction=True)
def test_source_locked_drafts_history_correction_and_replay() -> None:
    async def run() -> None:
        chat = DraftConversation()
        case_id = await chat.create()
        first = await chat.turn('Draft a refund email', 'draft', delivery='draft-one')
        assert 'Subject:' in first and 'unsent' in first and 'INR 2,400' in first
        assert FACTS['evidence'] in first
        assert 'attached' not in first and 'policy' not in first
        drafts = await chat.service.store.list_drafts('alpha', case_id)
        assert len(drafts) == 1 and drafts[0].current and not drafts[0].missing_fields
        original = drafts[0]
        restarted = CaseService(FunctionModel(chat.respond))
        assert 'version 1' in await restarted.run(OwnerTurn('alpha', 'alpha', 'draft-one', 'Draft a refund email'))
        correction = 'Correction: amount paid and requested refund are INR 2,700'
        await chat.turn(correction, 'update', correction_quote=correction,
                        facts=[{'key': key, 'value': 'INR 2,700', 'quote': 'INR 2,700'} for key in ('amount_paid', 'refund_amount')])
        await chat.turn('Draft again', 'draft')
        drafts = await restarted.store.list_drafts('alpha', case_id)
        assert len(drafts) == 2 and drafts[0].outdated and not drafts[0].current
        assert drafts[0].body == original.body and drafts[0].subject == original.subject
        assert drafts[1].current and drafts[1].fact_revision == 2
        assert 'INR 2,700' in drafts[1].body and 'INR 2,400' not in drafts[1].body
        assert (await restarted.store.get_draft('alpha', case_id)) == drafts[1]
        assert 'outdated' in await chat.turn('List drafts', 'list_drafts')
        assert 'INR 2,400' in await chat.turn('Read version 1', 'read_draft', draft_version=1)
        current = await restarted.store.get_case('alpha', case_id)
        assert current and current.status == 'open'
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_unknown_refund_never_defaults_to_paid_and_model_cannot_supply_prose() -> None:
    async def run() -> None:
        chat = DraftConversation()
        facts = {key: value for key, value in FACTS.items() if key not in ('refund_amount', 'purchase_date')}
        case_id = await chat.create(facts=facts)
        response = await chat.turn('Draft an incomplete refund request', 'draft')
        drafts = await chat.service.store.list_drafts('alpha', case_id)
        assert 'INCOMPLETE' in response and 'purchase date' in response and 'refund amount' in response
        assert drafts[0].missing_fields == ('purchase_date', 'refund_amount')
        assert not drafts[0].current
        assert 'I request a refund of [requested refund amount' in drafts[0].body
        assert 'I request a refund of INR 2,400' not in drafts[0].body
        assert await chat.service.store.get_draft('alpha', case_id) is None
        chat.plan['body'] = 'I am legally entitled to INR 999,999; attached receipt.'
        with pytest.raises((UsageLimitExceeded, UnexpectedModelBehavior)):
            await chat.turn('Draft again', 'draft')
        assert len(await chat.service.store.list_drafts('alpha', case_id)) == 1
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('mutation', ['correction', 'selection', 'deletion'])
def test_late_generation_cannot_save_after_case_state_changes(mutation: str) -> None:
    async def run() -> None:
        chat = DraftConversation()
        case_id = await chat.create()
        started, release = asyncio.Event(), asyncio.Event()
        async def pause() -> None:
            started.set()
            await release.wait()
        chat.generation = pause
        pending = asyncio.create_task(chat.turn('Draft refund email', 'draft'))
        await asyncio.wait_for(started.wait(), 5)
        other = DraftConversation()
        other.delivery = 100
        if mutation == 'correction':
            await other.turn('Correction: refund amount INR 2,700', 'update',
                correction_quote='Correction: refund amount INR 2,700',
                facts=[{'key': 'refund_amount', 'value': 'INR 2,700', 'quote': 'INR 2,700'}])
        elif mutation == 'selection':
            await other.turn('Return to general research', 'leave', intent_quote='Return to general research')
        else:
            await sync_to_async(lambda: GrievanceCase.objects.filter(pk=case_id).delete())()
        release.set()
        assert 'No draft was saved' in await pending
        assert await CaseStore().list_drafts('alpha', case_id) == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_parallel_delivery_commits_one_version() -> None:
    async def run() -> None:
        chat = DraftConversation()
        case_id = await chat.create()
        started, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def pause() -> None:
            nonlocal count
            count += 1
            if count == 2:
                started.set()
            await release.wait()
        chat.generation = pause
        turns = [asyncio.create_task(chat.turn('Draft refund email', 'draft', delivery='same-draft')) for _ in range(2)]
        await asyncio.wait_for(started.wait(), 5)
        release.set()
        replies = await asyncio.gather(*turns)
        assert any('already processed' in reply for reply in replies)
        assert len(await CaseStore().list_drafts('alpha', case_id)) == 1
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_draft_owner_and_selected_case_isolation_and_unavailable_references() -> None:
    async def run() -> None:
        chat = DraftConversation()
        case_a = await chat.create()
        await chat.turn('Draft refund email', 'draft')
        hidden = await chat.create(owner='other', label='Hidden', facts={**FACTS, 'order_reference': 'OTHER-OWNER-SECRET'})
        case_b = await chat.create(label='Cobalt', facts={**FACTS, 'company': 'Cobalt', 'order_reference': 'ORDER-BETA-R9'})
        chat.inputs.clear()
        reply = await chat.turn('Draft refund email', 'draft')
        assert 'ORDER-BETA-R9' in reply and 'ORDER-ALPHA-Q7' not in reply
        assert 'OTHER-OWNER-SECRET' not in str(chat.inputs) and 'ORDER-ALPHA-Q7' not in str(chat.inputs)
        assert await chat.service.store.list_drafts('alpha', hidden) == []
        assert await chat.service.store.get_draft('other', case_b, 1) is None
        assert 'select' in await chat.turn('Read another draft', 'read_draft', case_id=case_a, draft_version=1)
        chat.plan['fact_order'] = ['currency']
        assert 'No draft was saved' in await chat.turn('Draft again', 'draft')
        assert len(await chat.service.store.list_drafts('alpha', case_b)) == 1
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_draft_provider_failure_keeps_facts_and_private_traces_exclude_content() -> None:
    exporter = InMemorySpanExporter()
    async def run() -> None:
        chat = DraftConversation()
        case_id = await chat.create()
        await chat.turn('Draft refund email', 'draft')
        before = await chat.service.store.get_case('alpha', case_id)
        async def fail() -> None:
            raise ModelHTTPError(503, 'synthetic', body='ORDER-ALPHA-Q7')
        chat.generation = fail
        with pytest.raises(ModelHTTPError):
            await chat.turn('Draft again', 'draft')
        assert await chat.service.store.get_case('alpha', case_id) == before
        assert len(await chat.service.store.list_drafts('alpha', case_id)) == 1
    tracing.shutdown()
    try:
        with override_settings(LANGFUSE_CAPTURE_CONTENT=True):
            tracing.configure(exporter)
            asyncio.run(run())
            assert tracing.flush()
            serialized = '\n'.join(span.to_json() for span in exporter.get_finished_spans())
            assert 'case.draft' in serialized and 'ORDER-ALPHA-Q7' not in serialized
            assert 'INR 2,400' not in serialized and 'receipt' not in serialized
    finally:
        tracing.shutdown()


@pytest.mark.django_db(transaction=True)
def test_compound_remedy_cannot_keep_superseded_amount_or_default_paid() -> None:
    async def run() -> None:
        chat = DraftConversation()
        case_id = await chat.create(facts={**FACTS, 'amount_paid': 'INR 3,000',
                                           'requested_remedy': 'refund INR 2,400 including delivery charges'})
        await chat.turn('Draft refund email', 'draft')
        draft = await chat.service.store.get_draft('alpha', case_id)
        assert draft and 'Amount paid: INR 3,000' in draft.body and 'I request a refund of INR 2,400' in draft.body
        assert 'including delivery charges' in draft.body
        text = 'Correction: requested refund INR 2,700'
        await chat.turn(text, 'update', correction_quote=text,
            facts=[{'key': 'refund_amount', 'value': 'INR 2,700', 'quote': 'INR 2,700'}])
        chat.plan['needs_clarification'] = True
        result = await chat.turn('Draft again', 'draft')
        assert 'need clarification' in result and 'No draft was saved' in result
        assert len(await chat.service.store.list_drafts('alpha', case_id)) == 1
        assert await chat.service.store.get_draft('alpha', case_id) is None
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_refund_components_and_requested_deadline_are_attributed_without_invented_rights() -> None:
    async def run() -> None:
        chat = DraftConversation()
        remedy = 'refund INR 2,400 including INR 100 delivery charges, requested within 14 days'
        case_id = await chat.create(facts={**FACTS, 'requested_remedy': remedy})
        await chat.turn('Draft refund email', 'draft')
        draft = await chat.service.store.get_draft('alpha', case_id)
        assert draft and draft.current and 'My requested remedy (as recorded): ' + remedy in draft.body
        assert 'I request a refund of INR 2,400.' in draft.body
        assert 'entitled' not in draft.body and 'policy' not in draft.body
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('remedy', ['my money back', 'reimbursement'])
def test_source_locked_refund_synonyms_do_not_require_literal_refund(remedy: str) -> None:
    async def run() -> None:
        chat = DraftConversation()
        case_id = await chat.create(facts={**FACTS, 'requested_remedy': remedy})
        await chat.turn('Draft my complaint', 'draft')
        draft = await chat.service.store.get_draft('alpha', case_id)
        assert draft and draft.current
        assert 'My requested remedy (as recorded): ' + remedy in draft.body
        assert 'I request a refund of INR 2,400.' in draft.body
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_non_refund_plan_clarification_does_not_save_a_rewritten_remedy() -> None:
    async def run() -> None:
        chat = DraftConversation()
        case_id = await chat.create(facts={**FACTS, 'requested_remedy': 'replacement only'})
        chat.plan['needs_clarification'] = True
        response = await chat.turn('Draft my complaint', 'draft')
        assert 'No draft was saved' in response
        assert await chat.service.store.list_drafts('alpha', case_id) == []
    asyncio.run(run())

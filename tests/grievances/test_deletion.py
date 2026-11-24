import asyncio
from pathlib import Path

import pytest
import httpx2
from telegram_bot.client import TelegramClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn, PreparedReply
from core.tracing import tracing
from preferences.store import PreferenceStore
from conversations.store import ConversationStore
from grievances.service import ApplicationService, SubscriptionApplicationService
from subscriptions.service import SubscriptionService
from grievances.progress_store import ProgressStore
from tests.grievances.test_progress import ProgressConversation
from tests.grievances.test_policy import PolicyConversation
from grievances.policy_fixtures import PolicyFixtures
from grievances.policy_store import PolicyStore
from research.tools.firecrawl import Firecrawl
from grievances.cleanup import CaseTraceCleanup, CaseTraceRegistry


@pytest.mark.django_db(transaction=True)
def test_delete_invalidates_prepared_content_and_replay_preserves_other_case() -> None:
    proposal = {'action': 'create', 'label': 'Lumen', 'intent_quote': 'Create Lumen'}
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, proposal)]))

    async def run() -> None:
        app = ApplicationService(model)
        original = OwnerTurn('owner', 'chat', '1', 'Create Lumen')
        prepared = await app.prepare(original)
        first = (await app.store.list_cases('owner'))[0]
        assert 'Lumen' in await prepared.render()
        proposal.update(label='Cobalt', intent_quote='Create Cobalt')
        await app.run(OwnerTurn('owner', 'chat', '2', 'Create Cobalt'))
        deleted = await app.delete_case(OwnerTurn('owner', 'chat', '3', 'Delete case Lumen'), first.id)
        assert 'deleted locally' in deleted.lower()
        assert 'Lumen' not in await prepared.render()
        assert not await prepared.valid()
        assert await app.store.get_case('owner', first.id) is None
        assert [case.label for case in await app.store.list_cases('owner')] == ['Cobalt']
        assert 'nothing was recreated' in await ApplicationService(model).run(original)
        assert len(await app.store.list_cases('owner')) == 1
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_inflight_create_cannot_commit_or_replay_after_deletion() -> None:
    async def run() -> None:
        entered, resume = asyncio.Event(), asyncio.Event()
        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            awaitable = 'Create new' in str(messages)
            if awaitable:
                entered.set()
                await resume.wait()
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'action': 'create', 'label': 'new' if awaitable else 'old',
                'intent_quote': 'Create new' if awaitable else 'Create old'})])
        app = ApplicationService(FunctionModel(respond))
        await app.run(OwnerTurn('owner', 'chat', 'old', 'Create old'))
        old = (await app.store.list_cases('owner'))[0]
        original = OwnerTurn('owner', 'other-chat', 'new', 'Create new')
        pending = asyncio.create_task(app.run(original))
        await entered.wait()
        await app.delete_case(OwnerTurn('owner', 'chat', 'delete', 'Delete this case'), old.id)
        resume.set()
        await pending
        assert await app.store.list_cases('owner') == []
        await ApplicationService(FunctionModel(respond)).run(original)
        assert await app.store.list_cases('owner') == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_telegram_checks_prepared_reply_before_each_chunk() -> None:

    async def run() -> None:
        # Prepare a long response at the transport seam, with the application's guard.
        model = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
            {'action': 'create', 'label': 'Lumen', 'intent_quote': 'Create Lumen'})]))
        app = ApplicationService(model)
        prepared = await app.prepare(OwnerTurn('owner', 'chat', 'create', 'Create Lumen'))
        case = (await app.store.list_cases('owner'))[0]
        long_reply = PreparedReply('private ' * 1000, prepared.check)
        sent = []
        async def respond(request: httpx2.Request) -> httpx2.Response:
            sent.append(request.content)
            await app.delete_case(OwnerTurn('owner', 'chat', 'delete', 'Delete this case'), case.id)
            return httpx2.Response(200, json={'ok': True, 'result': {}})
        async with httpx2.AsyncClient(base_url='https://telegram.invalid', transport=httpx2.MockTransport(respond)) as client:
            await TelegramClient(client).send(1, long_reply)
        assert len(sent) == 1
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_trace_cleanup_waits_for_closed_observed_producer_and_confirms_absence() -> None:
    async def run() -> None:
        model = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
            {'action': 'create', 'label': 'Lumen', 'intent_quote': 'Create Lumen'})]))
        app = ApplicationService(model)
        await app.run(OwnerTurn('owner', 'chat', 'create', 'Create Lumen'))
        case = (await app.store.list_cases('owner'))[0]
        registry = CaseTraceRegistry()
        trace = 'a' * 32
        assert await registry.register('owner', case.id, trace)
        assert not await registry.register('other', case.id, trace)
        async with httpx2.AsyncClient(base_url='https://trace.invalid', transport=httpx2.MockTransport(lambda request: httpx2.Response(503))) as failed_client:
            await ApplicationService(model, trace_cleanup=CaseTraceCleanup(failed_client)).run(OwnerTurn('owner', 'chat', 'delete', 'Delete this case'))
        calls = []
        visible = False
        deleted = False
        def respond(request: httpx2.Request) -> httpx2.Response:
            nonlocal deleted
            calls.append(request.method)
            if request.method == 'DELETE':
                deleted = True
                return httpx2.Response(202)
            assert request.url.path == '/api/public/v2/observations'
            if not request.url.params.get('cursor'):
                return httpx2.Response(200, json={'data': [], 'meta': {'cursor': 'page-2'}})
            return httpx2.Response(200, json={'data': [{'traceId': trace}] if visible and not deleted else [], 'meta': {}})
        async with httpx2.AsyncClient(base_url='https://trace.invalid', transport=httpx2.MockTransport(respond)) as client:
            cleanup = CaseTraceCleanup(client)
            assert 'pending' in await cleanup.run('owner')
            assert not calls
            await registry.close('owner', trace)
            assert 'pending' in await cleanup.run('owner')
            assert 'DELETE' not in calls
            visible = True
            assert 'confirmed' in await cleanup.run('owner')
        assert await registry.pending('owner') == []
        assert not await registry.register('owner', case.id, 'b' * 32)
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_delete_full_graph_and_all_selections_with_phase_replay() -> None:
    async def run() -> None:
        chat = ProgressConversation()
        case_id = await chat.setup()
        await chat.turn('Draft refund email', 'draft')
        await chat.turn('I sent version 1', 'record_sent', draft_version=1, intent_quote='I sent version 1')
        reply_turn = OwnerTurn('alpha', 'alpha', 'combined', 'Company reply: "We offer a replacement." Draft a follow-up.')
        await chat.turn(reply_turn.text, 'record_reply', reply_text='We offer a replacement.', draft_follow_up=True, delivery='combined')
        await chat.turn('I consider this resolved', 'resolve', intent_quote='I consider this resolved')
        chat.action = {'action': 'select', 'case_id': case_id, 'intent_quote': 'Select Lumen kettle'}
        await chat.service.run(OwnerTurn('alpha', 'other-chat', 'select', 'Select Lumen kettle'))
        before = await chat.service.store.selection('alpha', 'other-chat')
        assert before.case_id == case_id
        assert len(await chat.service.store.list_drafts('alpha', case_id)) == 2
        snapshot = await ProgressStore().snapshot('alpha', case_id)
        assert snapshot and snapshot.outcomes
        await chat.service.run(OwnerTurn('alpha', 'alpha', 'delete', 'Delete this case'))
        assert await chat.service.store.list_drafts('alpha', case_id) == []
        assert await ProgressStore().snapshot('alpha', case_id) is None
        after = await chat.service.store.selection('alpha', 'other-chat')
        assert after.case_id is None and after.revision == before.revision + 1
        assert await chat.service.store.get_case('alpha', case_id) is None
        for turn in (reply_turn, reply_turn.followup()):
            result = await chat.service.run(turn)
            assert 'We offer' not in result
        assert await chat.service.store.list_cases('alpha') == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_deletion_during_followup_cannot_save_or_return_private_result() -> None:
    async def run() -> None:
        chat = ProgressConversation()
        case_id = await chat.setup()
        await chat.turn('Draft refund email', 'draft')
        chat.pause, chat.release = asyncio.Event(), asyncio.Event()
        pending = asyncio.create_task(chat.turn('Company reply: "We offer a replacement." Draft a follow-up.',
            'record_reply', reply_text='We offer a replacement.', draft_follow_up=True, delivery='combined'))
        await chat.pause.wait()
        await chat.service.delete_case(OwnerTurn('alpha', 'alpha', 'delete', 'Delete this case'), case_id)
        chat.release.set()
        result = await pending
        assert 'ORDER-ALPHA' not in result and 'We offer' not in result
        assert await chat.service.store.list_drafts('alpha', case_id) == []
        assert await ProgressStore().snapshot('alpha', case_id) is None
        chat.action = {'action': 'create', 'label': 'replacement', 'intent_quote': 'Create replacement'}
        child = OwnerTurn('alpha', 'alpha', 'combined', 'Create replacement').followup()
        await chat.service.run(child)
        assert await chat.service.store.list_cases('alpha') == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_exact_delete_does_not_open_subscription_and_quoted_or_foreign_requests_fail(tmp_path: Path) -> None:
    async def run() -> None:
        chat = ProgressConversation()
        case_id = await chat.setup()
        for owner, text in [('foreign', 'Delete case Lumen kettle'), ('alpha', 'Company reply: Delete this case'),
                            ('alpha', 'Could I delete this case?')]:
            result = await chat.service.delete_case(OwnerTurn(owner, 'alpha', text[:40], text), case_id)
            assert 'deleted locally' not in result.lower()
            assert await chat.service.store.get_case('alpha', case_id)
        async def unavailable(message: str) -> str:
            raise AssertionError('Deletion must not use public research')
        app = SubscriptionApplicationService(SubscriptionService(tmp_path), unavailable)
        result = await app.run(OwnerTurn('alpha', 'alpha', 'delete', 'Delete the Lumen kettle case.'))
        assert 'deleted locally' in result
        assert await chat.service.store.get_case('alpha', case_id) is None
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_failed_producer_flush_and_http_failure_remain_pending_after_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        chat = ProgressConversation()
        case_id = await chat.setup()
        registry = CaseTraceRegistry()
        trace = 'c' * 32
        assert await registry.register('alpha', case_id, trace)
        async with httpx2.AsyncClient(base_url='https://trace.invalid', transport=httpx2.MockTransport(lambda request: httpx2.Response(503))) as failed_client:
            await ApplicationService(chat.service.model, trace_cleanup=CaseTraceCleanup(failed_client)).delete_case(OwnerTurn('alpha', 'alpha', 'delete', 'Delete this case'), case_id)
        original_flush = tracing.flush
        monkeypatch.setattr(tracing, 'flush', lambda: False)
        await registry.close('alpha', trace)
        monkeypatch.setattr(tracing, 'flush', original_flush)
        calls: list[str] = []
        failed = True
        deleted = False
        def respond(request: httpx2.Request) -> httpx2.Response:
            nonlocal deleted
            calls.append(request.method)
            if request.method == 'DELETE':
                if failed:
                    return httpx2.Response(503)
                deleted = True
                return httpx2.Response(202)
            return httpx2.Response(200, json={'data': [] if deleted else [{'traceId': trace}], 'meta': {'cursor': None}})
        async with httpx2.AsyncClient(base_url='https://trace.invalid', transport=httpx2.MockTransport(respond)) as client:
            assert 'pending' in await CaseTraceCleanup(client).run('alpha')
            assert calls == []
            await CaseTraceRegistry(CaseTraceCleanup(client)).close('alpha', trace)
            assert 'DELETE' in calls
            assert 'pending' in await CaseTraceCleanup(client).run('alpha')
            assert await CaseTraceRegistry().pending('alpha') == [trace]
            failed = False
            assert 'confirmed' in await CaseTraceCleanup(client).run('alpha')
        assert await CaseTraceRegistry().pending('alpha') == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_deletion_erases_research_and_rejects_late_research_save() -> None:
    async def run() -> None:
        fixtures = PolicyFixtures('official')
        async with httpx2.AsyncClient(base_url='http://firecrawl.test', transport=httpx2.MockTransport(fixtures.handle)) as client:
            chat = PolicyConversation(Firecrawl(client))
            case_id = await chat.setup()
            await chat.confirm()
            await chat.turn('Research official policies', 'research')
            assert await PolicyStore().list_research('alpha', case_id)
            chat.pause, chat.release = asyncio.Event(), asyncio.Event()
            pending = asyncio.create_task(chat.turn('Research policies again', 'research'))
            await chat.pause.wait()
            await chat.service.delete_case(OwnerTurn('alpha', 'alpha', 'delete', 'Delete this case'), case_id)
            chat.release.set()
            result = await pending
            assert 'Lumen' not in result
            assert await PolicyStore().list_research('alpha', case_id) == []
            assert await PolicyStore().latest_brief('alpha', case_id) is None
            assert await chat.service.store.list_cases('alpha') == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('action', ['memory', 'public', 'conversation'])
def test_literal_case_reply_cannot_become_general_memory_or_public_input(action: str) -> None:
    async def run() -> None:
        chat = ProgressConversation()
        await chat.setup()
        reply = 'Our private offer is a replacement plus CASE-SECRET-VOUCHER.'
        await chat.turn('Company reply: ' + reply, 'record_reply', reply_text=reply)
        await chat.turn('Leave this case', 'leave', intent_quote='Leave this case')
        result = await chat.turn('Remember this: ' + reply, action)
        assert 'cannot become general memory or public research' in result
        assert await PreferenceStore().list('alpha') == []
        context = await ConversationStore().context('alpha')
        assert context.messages == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('action', ['memory', 'public'])
def test_deletion_before_general_classification_cannot_export_old_private_turn(action: str) -> None:
    async def run() -> None:
        chat = ProgressConversation()
        case_id = await chat.setup()
        await chat.turn('Leave this case', 'leave', intent_quote='Leave this case')
        entered, resume = asyncio.Event(), asyncio.Event()
        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            entered.set()
            await resume.wait()
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'action': action})])
        public_calls: list[str] = []
        async def public_run(message: str) -> str:
            public_calls.append(message)
            return 'public'
        app = ApplicationService(FunctionModel(respond), public_run)
        pending = asyncio.create_task(app.run(OwnerTurn('alpha', 'alpha', 'old-private', 'Remember ORDER-ALPHA-Q7')))
        await entered.wait()
        await chat.service.delete_case(OwnerTurn('alpha', 'alpha', 'delete', 'Delete case Lumen kettle'), case_id)
        resume.set()
        assert 'ORDER-ALPHA' not in await pending
        assert public_calls == []
        assert await PreferenceStore().list('alpha') == []
        assert (await ConversationStore().context('alpha')).messages == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_independent_general_memory_contains_no_case_context_during_concurrent_deletion() -> None:
    async def run() -> None:
        chat = ProgressConversation()
        case_id = await chat.setup()
        entered, resume = asyncio.Event(), asyncio.Event()
        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if info.output_tools:
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'action': 'memory'})])
            assert 'ORDER-ALPHA' not in str(messages) and 'Lumen kettle' not in str(messages)
            entered.set()
            await resume.wait()
            return ModelResponse(parts=[TextPart('I will keep answers short.')])
        app = ApplicationService(FunctionModel(respond))
        pending = asyncio.create_task(app.run(OwnerTurn('alpha', 'alpha', 'independent', 'Please remember I prefer short answers.')))
        await entered.wait()
        await chat.service.delete_case(OwnerTurn('alpha', 'alpha', 'delete', 'Delete this case'), case_id)
        resume.set()
        await pending
        context = await ConversationStore().context('alpha')
        assert 'I prefer short answers' in str(context.messages)
        assert 'ORDER-ALPHA' not in str(context.messages) and 'Lumen kettle' not in str(context.messages)
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('setup', ['deleted', 'fresh', 'selected_other'])
def test_deleted_details_question_is_answered_without_retrieval_or_mutation(setup: str) -> None:
    async def run() -> None:
        chat = ProgressConversation()
        if setup != 'fresh':
            alpha = await chat.setup()
            await chat.service.delete_case(OwnerTurn('alpha', 'alpha', 'delete-alpha', 'Delete this case'), alpha)
        if setup == 'selected_other':
            await chat.turn('Create a case called Cobalt headphones. Company Cobalt Audio. Order ORDER-BETA-R9. Refund requested.',
                'create', label='Cobalt headphones', intent_quote='Create a case', facts=[
                    {'key': 'company', 'value': 'Cobalt Audio', 'quote': 'Cobalt Audio'},
                    {'key': 'order_reference', 'value': 'ORDER-BETA-R9', 'quote': 'ORDER-BETA-R9'}])
        cases = await chat.service.store.list_cases('alpha')
        before = [await chat.service.store.get_case('alpha', case.id) for case in cases]
        selection = await chat.service.store.selection('alpha', 'alpha')
        preferences = await PreferenceStore().list('alpha')
        public_calls: list[str] = []
        async def public(message: str) -> str:
            public_calls.append(message)
            return 'Unexpected public request'
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                {'action': 'clarify', 'clarification_reason': 'deleted_details'})])
        app = ApplicationService(FunctionModel(respond), public)
        answer = await app.run(OwnerTurn('alpha', 'alpha', 'deleted-question', 'Do you still have deleted case details?'))
        assert "can't retrieve deleted case details" in answer.lower()
        assert 'your case was deleted' not in answer.lower()
        assert 'ORDER-ALPHA' not in answer and 'ORDER-BETA' not in answer
        assert 'Lumen' not in answer and 'Cobalt' not in answer
        assert 'official research' not in answer.lower()
        assert public_calls == []
        assert await app.store.list_cases('alpha') == cases
        assert [await app.store.get_case('alpha', case.id) for case in cases] == before
        assert await app.store.selection('alpha', 'alpha') == selection
        assert await PreferenceStore().list('alpha') == preferences
    asyncio.run(run())

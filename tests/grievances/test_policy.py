import asyncio
import json
from typing import Any

import httpx2
import pytest
from django.test import override_settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.tracing import tracing
from core.turns import OwnerTurn
from grievances.policy_fixtures import PolicyFixtures
from grievances.policy_store import PolicyStore
from grievances.service import CaseService
from research.tools.firecrawl import Firecrawl


BRIEF = {'company': 'Lumen Home', 'product': 'electrical appliance', 'issue': 'defective item',
         'jurisdiction': 'India', 'official_site': 'https://lumen-home.test/'}
FACTS = {'company': 'Lumen Home', 'purchase_context': 'kettle', 'issue': 'did not turn on',
         'purchase_date': '2026-11-02', 'amount_paid': 'INR 2,400', 'refund_amount': 'INR 2,400',
         'requested_remedy': 'refund', 'order_reference': 'ORDER-ALPHA-Q7', 'customer_name': 'PRIVATE-NAME-ALPHA',
         'address': 'PRIVATE-ADDRESS-ALPHA', 'correspondence': 'PRIVATE-MAIL-ALPHA'}


class PolicyConversation:
    def __init__(self, firecrawl: Firecrawl, scenario: str = 'official') -> None:
        self.action: dict[str, Any] = {}
        self.inputs: list[str] = []
        self.public_inputs: list[str] = []
        self.scenario = scenario
        self.delivery = 0
        self.pause: asyncio.Event | None = None
        self.release: asyncio.Event | None = None
        self.service = CaseService(FunctionModel(self.respond), firecrawl=firecrawl)

    async def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        schema = info.output_tools[0].parameters_json_schema.get('properties', {})
        self.inputs.append(str(messages))
        result = self.action
        if 'identity_quote' in schema:
            self.public_inputs.append(str(messages))
            if self.pause and self.release:
                self.pause.set()
                await self.release.wait()
            result = {'identity_source_id': 'source-1',
                      'identity_quote': 'Lumen Home is the company responsible for these appliances.',
                      'findings': [{'source_id': 'source-2', 'quote': PolicyFixtures.CURRENT, 'kind': 'policy', 'effective_from': '2026-11-15'},
                                   {'source_id': 'source-3', 'quote': 'Contact help@lumen-home.test. Include a concise description and order reference. Do not send payment card details.', 'kind': 'support'}],
                      'conflicting': self.scenario == 'conflicting_policies'}
            if self.scenario not in ('historical_gap', 'conflicting_policies'):
                result['findings'].append({'source_id': 'source-4', 'quote': PolicyFixtures.HISTORIC, 'kind': 'policy',
                                           'effective_from': '2026-10-01', 'effective_until': '2026-11-14'})
            if self.scenario == 'fabricated':
                result['findings'] = [{'source_id': 'source-2', 'quote': 'Guaranteed refund of INR 999,999', 'kind': 'policy'}]
        elif 'fact_order' in schema:
            if self.pause and self.release:
                self.pause.set()
                await self.release.wait()
            result = {'fact_order': ['issue', 'purchase_date']}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])

    async def turn(self, text: str, action: str, **fields: Any) -> str:
        self.delivery += 1
        self.action = {'action': action, **fields}
        return await self.service.run(OwnerTurn('alpha', 'alpha', str(self.delivery), text))

    async def setup(self) -> str:
        await self.turn('Create a case called Lumen kettle. ' + '\n'.join(FACTS.values()), 'create',
                        label='Lumen kettle', intent_quote='Create a case',
                        facts=[{'key': key, 'value': value, 'quote': value} for key, value in FACTS.items()])
        case = (await self.service.store.list_cases('alpha'))[0]
        await self.turn('Review public research fields: ' + json.dumps(BRIEF), 'review_research', public_brief=BRIEF)
        return case.id

    async def confirm(self) -> str:
        return await self.turn('I confirm these public research fields and this official site.', 'confirm_research')


@pytest.mark.django_db(transaction=True)
def test_review_confirmation_official_evidence_and_immutable_grounded_draft() -> None:
    async def run() -> None:
        fixtures = PolicyFixtures('official')
        async with httpx2.AsyncClient(base_url='http://firecrawl.test', transport=httpx2.MockTransport(fixtures.handle)) as client:
            chat = PolicyConversation(Firecrawl(client))
            case_id = await chat.setup()
            assert not fixtures.requests
            assert 'first' in await chat.turn('Research policies', 'research')
            assert not fixtures.requests
            assert 'explicitly say' in await chat.turn('https://lumen-home.test/', 'confirm_research')
            assert not fixtures.requests
            assert 'owner-confirmed' in await chat.confirm()
            await chat.turn('Draft refund email', 'draft')
            original = (await chat.service.store.list_drafts('alpha', case_id))[0]
            result = await chat.turn('Research official policies', 'research')
            assert 'verified_sources' in result
            evidence = (await PolicyStore().list_research('alpha', case_id))[0]
            assert len(evidence.findings) == 3 and evidence.brief_revision == 1
            assert evidence.findings[0].applicability == 'published_after_purchase'
            assert evidence.findings[2].applicability == 'purchase_time_candidate_not_eligibility'
            assert all(source.trust_basis == 'owner_confirmed' and source.identity_quote for source in evidence.sources)
            assert evidence.sources[1].linked_from == BRIEF['official_site']
            assert all(secret not in str(chat.public_inputs) for secret in PolicyFixtures.PRIVATE)
            assert '2026-11-02' not in str(chat.public_inputs) and 'INR 2,400' not in str(chat.public_inputs)
            assert all(secret not in json.dumps(fixtures.requests) for secret in PolicyFixtures.PRIVATE)
            await chat.turn('Draft using research', 'draft')
            drafts = await chat.service.store.list_drafts('alpha', case_id)
            assert drafts[0].body == original.body and drafts[0].subject == original.subject and drafts[1].evidence_revision == 1
            assert PolicyFixtures.CURRENT in drafts[1].body and 'https://lumen-home.test/returns' in drafts[1].body
            assert 'no refund entitlement is asserted' in drafts[1].body
            await chat.service.run(OwnerTurn('alpha', 'alpha', str(chat.delivery - 1), 'Research official policies'))
            assert len(await PolicyStore().list_research('alpha', case_id)) == 1
            await chat.turn('Research policies again', 'research')
            after = await chat.service.store.list_drafts('alpha', case_id)
            assert after[1].outdated and after[1].body == drafts[1].body
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('scenario', ['impostor_first', 'historical_gap', 'conflicting_policies', 'injection_privacy', 'unavailable', 'fabricated'])
def test_research_failure_conflict_injection_and_factual_fallback(scenario: str) -> None:
    async def run() -> None:
        fixtures = PolicyFixtures(scenario)
        async with httpx2.AsyncClient(base_url='http://firecrawl.test', transport=httpx2.MockTransport(fixtures.handle)) as client:
            chat = PolicyConversation(Firecrawl(client), scenario)
            case_id = await chat.setup()
            await chat.confirm()
            await chat.turn('Research policies and do not search my ORDER-ALPHA-Q7 or PRIVATE-MAIL-ALPHA', 'research')
            evidence = (await PolicyStore().list_research('alpha', case_id))[0]
            if scenario in ('unavailable', 'fabricated'):
                assert evidence.status == 'unverified' and not evidence.findings
            if scenario == 'historical_gap':
                assert not any(item.applicability == 'purchase_time_candidate_not_eligibility' for item in evidence.findings)
            if scenario == 'conflicting_policies':
                assert 'conflict' in evidence.uncertainty
            assert '90 days' not in str(evidence.findings) and '999,999' not in str(evidence.findings)
            assert all(secret not in json.dumps(fixtures.requests) for secret in PolicyFixtures.PRIVATE)
            assert all('lumen-home-refunds.test' not in str(request.get('body', {}).get('url', '')) for request in fixtures.requests)
            await chat.turn('Draft refund email', 'draft')
            draft = await chat.service.store.get_draft('alpha', case_id)
            assert draft and 'INR 2,400' in draft.body and 'SYSTEM:' not in draft.body
            case = await chat.service.store.get_case('alpha', case_id)
            assert case and case.status == 'open'
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('mode', ['unmarked', 'redirect', 'truncated', 'private_link'])
def test_final_target_and_actual_url_allowlist(mode: str) -> None:
    async def run() -> None:
        fixtures = PolicyFixtures('official')
        def handle(request: httpx2.Request) -> httpx2.Response:
            response = fixtures.handle(request)
            payload = response.json()
            if request.url.path == '/v2/scrape':
                if mode == 'unmarked':
                    payload['data']['metadata'].pop('finalUrlVerified', None)
                elif mode == 'redirect':
                    payload['data']['metadata']['url'] = 'https://impostor.test/'
                elif mode == 'truncated':
                    payload['data']['markdown'] = 'x' * 130001
                elif mode == 'private_link' and json.loads(request.content)['url'].endswith('/'):
                    payload['data']['links'] += ['https://lumen-home.test/PRIVATE-MAIL-ALPHA',
                                                 'https://lumen-home.test/%50RIVATE-MAIL-ALPHA',
                                                 'https://lumen-home.test/?order=ORDER-ALPHA-Q7']
            return httpx2.Response(200, json=payload)
        async with httpx2.AsyncClient(base_url='http://firecrawl.test', transport=httpx2.MockTransport(handle)) as client:
            chat = PolicyConversation(Firecrawl(client))
            case_id = await chat.setup()
            await chat.confirm()
            await chat.turn('Research policies', 'research')
            evidence = (await PolicyStore().list_research('alpha', case_id))[0]
            assert evidence.status == ('verified_sources' if mode == 'private_link' else 'unverified')
            assert all(secret not in json.dumps(fixtures.requests) for secret in PolicyFixtures.PRIVATE)
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_private_public_field_is_rejected_before_web_and_stale_evidence_does_not_commit() -> None:
    async def run() -> None:
        fixtures = PolicyFixtures('official')
        async with httpx2.AsyncClient(base_url='http://firecrawl.test', transport=httpx2.MockTransport(fixtures.handle)) as client:
            chat = PolicyConversation(Firecrawl(client))
            case_id = await chat.setup()
            result = await chat.turn('Review a private company', 'review_research', public_brief={**BRIEF, 'company': 'PRIVATE-NAME-ALPHA'})
            assert 'No web request' in result and not fixtures.requests
            await chat.confirm()
            chat.pause, chat.release = asyncio.Event(), asyncio.Event()
            pending = asyncio.create_task(chat.turn('Research policies', 'research'))
            await asyncio.wait_for(chat.pause.wait(), 5)
            other = PolicyConversation(Firecrawl(client))
            other.delivery = 100
            await other.turn('Correction: refund INR 2,700', 'update', correction_quote='Correction: refund INR 2,700',
                             facts=[{'key': 'refund_amount', 'value': 'INR 2,700', 'quote': 'INR 2,700'}])
            chat.release.set()
            assert 'changed' in await pending
            assert await PolicyStore().list_research('alpha', case_id) == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_policy_spans_remain_private_when_public_capture_is_enabled() -> None:
    exporter = InMemorySpanExporter()
    async def run() -> None:
        fixtures = PolicyFixtures('injection_privacy')
        async with httpx2.AsyncClient(base_url='http://firecrawl.test', transport=httpx2.MockTransport(fixtures.handle)) as client:
            chat = PolicyConversation(Firecrawl(client), 'injection_privacy')
            await chat.setup()
            await chat.confirm()
            await chat.turn('Research PRIVATE-MAIL-ALPHA policy', 'research')
    tracing.shutdown()
    try:
        with override_settings(LANGFUSE_CAPTURE_CONTENT=True):
            tracing.configure(exporter)
            asyncio.run(run())
            assert tracing.flush()
            serialized = '\n'.join(span.to_json() for span in exporter.get_finished_spans())
            assert 'case.policy_research' in serialized
            assert all(secret not in serialized for secret in PolicyFixtures.PRIVATE)
            assert 'lumen-home.test' not in serialized and PolicyFixtures.CURRENT not in serialized
    finally:
        tracing.shutdown()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('field,value', [('company', 'Cobalt Audio'), ('jurisdiction', 'Canada'),
                                         ('official_site', 'https://another-company.test/')])
def test_changed_public_brief_invalidates_prior_research_without_rewriting_history(field: str, value: str) -> None:
    async def run() -> None:
        fixtures = PolicyFixtures('official')
        async with httpx2.AsyncClient(base_url='http://firecrawl.test', transport=httpx2.MockTransport(fixtures.handle)) as client:
            chat = PolicyConversation(Firecrawl(client))
            case_id = await chat.setup()
            await chat.confirm()
            await chat.turn('Research policies', 'research')
            await chat.turn('Draft using research', 'draft')
            original = await chat.service.store.get_draft('alpha', case_id)
            assert original and original.evidence_revision == 1
            await chat.turn('Review corrected public fields', 'review_research', public_brief={**BRIEF, field: value})
            assert await chat.service.store.get_draft('alpha', case_id) is None
            history = await chat.service.store.get_draft('alpha', case_id, 1)
            assert history and history.outdated and history.body == original.body
            await chat.turn('Draft a factual request', 'draft')
            fallback = await chat.service.store.get_draft('alpha', case_id)
            assert fallback and fallback.evidence_revision is None and 'research is inactive' in fallback.body
            assert PolicyFixtures.CURRENT not in fallback.body
            await chat.confirm()
            await chat.turn('Draft while awaiting new research', 'draft')
            after_confirmation = await chat.service.store.get_draft('alpha', case_id)
            assert after_confirmation and after_confirmation.evidence_revision is None
            assert PolicyFixtures.CURRENT not in after_confirmation.body
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_inflight_draft_rejects_changed_public_brief() -> None:
    async def run() -> None:
        fixtures = PolicyFixtures('official')
        async with httpx2.AsyncClient(base_url='http://firecrawl.test', transport=httpx2.MockTransport(fixtures.handle)) as client:
            chat = PolicyConversation(Firecrawl(client))
            case_id = await chat.setup()
            await chat.confirm()
            await chat.turn('Research policies', 'research')
            chat.pause, chat.release = asyncio.Event(), asyncio.Event()
            pending = asyncio.create_task(chat.turn('Draft using research', 'draft'))
            await asyncio.wait_for(chat.pause.wait(), 5)
            other = PolicyConversation(Firecrawl(client))
            other.delivery = 100
            await other.turn('Review corrected jurisdiction', 'review_research', public_brief={**BRIEF, 'jurisdiction': 'Canada'})
            chat.release.set()
            assert 'Public research fields changed' in await pending
            assert await chat.service.store.list_drafts('alpha', case_id) == []
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_selected_research_route_receives_authoritative_confirmation_status() -> None:
    class StatusAwareConversation(PolicyConversation):
        async def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if self.action.get('action') == 'research' and 'You interpret grievance requests' in str(info):
                payload = str(messages)
                assert 'research_status' in payload
                assert '"confirmed": true' in payload
                assert '"matches_current_facts": true' in payload
            return await super().respond(messages, info)

    async def run() -> None:
        fixtures = PolicyFixtures('official')
        async with httpx2.AsyncClient(base_url='http://firecrawl.test', transport=httpx2.MockTransport(fixtures.handle)) as client:
            chat = StatusAwareConversation(Firecrawl(client))
            case_id = await chat.setup()
            await chat.confirm()
            assert 'verified_sources' in await chat.turn('Research official policies for this case', 'research')
            assert len(await PolicyStore().list_research('alpha', case_id)) == 1
    asyncio.run(run())

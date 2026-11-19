"""Private model planning with source-locked, immutable factual draft rendering."""

import json

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.tracing import tracing
from core.turns import OwnerTurn
from grievances import constants
from grievances.prompts import DRAFT_INSTRUCTIONS
from grievances.schema import DraftPlan
from grievances.store import CaseStore, CaseView, DraftContent, SelectionView


class InvalidDraftPlan(ValueError):
    pass


class DraftRenderer:
    """Only authoritative current values enter factual text; the model controls ordering."""

    def render(self, facts: dict[str, str], plan: DraftPlan) -> DraftContent:
        if plan.needs_clarification:
            raise InvalidDraftPlan('Current facts need clarification')
        if len(set(plan.fact_order)) != len(plan.fact_order) or any(key not in facts for key in plan.fact_order):
            raise InvalidDraftPlan('Unsupported fact reference')
        missing = tuple(key for key in constants.DRAFT_REQUIRED_FIELDS if not facts.get(key))
        company = ' '.join(facts.get('company', '[company]').split())
        product = ' '.join(facts.get('purchase_context', '[product or service]').split())
        subject = f'Refund request — {company} — {product}'
        paragraphs = ['Dear support team,' if plan.greeting == 'dear_support' else 'Hello,',
                      'I am writing to request a refund. Below is my account of the purchase and the problem.']
        labels = {'company': 'Company', 'purchase_context': 'Product or service',
                  'purchase_date': 'Purchase date', 'delivery_date': 'Delivery date',
                  'order_reference': 'Order reference', 'issue': 'Problem experienced',
                  'amount_paid': 'Amount paid', 'currency': 'Currency as recorded'}
        ordered = list(dict.fromkeys([*plan.fact_order, *constants.DRAFT_FACT_FIELDS]))
        for key in ordered:
            if key in labels and key in facts:
                paragraphs.append(f'{labels[key]}: {facts[key]}')
        if 'evidence' in facts:
            paragraphs.append('My description of the available or uncertain evidence (as recorded):\n' + facts['evidence'])
        if 'requested_remedy' in facts:
            paragraphs.append('My requested remedy (as recorded): ' + facts['requested_remedy'])
        amount = facts.get('refund_amount', '[requested refund amount — please supply]')
        paragraphs.append(f'I request a refund of {amount}. Please let me know the next steps and any information needed to review my request.')
        paragraphs.append('Thank you.')
        if missing:
            paragraphs.insert(0, 'INCOMPLETE DRAFT — please supply: ' + ', '.join(key.replace('_', ' ') for key in missing) + '.')
        return DraftContent(subject, '\n\n'.join(paragraphs), missing)


class DraftService:
    def __init__(self, model: Model, store: CaseStore) -> None:
        self.store = store
        self.agent: Agent[None, DraftPlan] = Agent(model, output_type=DraftPlan, instructions=DRAFT_INSTRUCTIONS,
            name='factual_draft', retries=constants.VALIDATION_RETRIES,
            model_settings={'max_tokens': constants.MAX_OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()

    async def run(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, usage: RunUsage) -> str:
        payload = json.dumps({'facts': current.facts,
                              'available_fact_keys': [key for key in constants.DRAFT_FACT_FIELDS if key in current.facts]})
        if len(payload) > constants.MAX_CONTEXT_CHARS:
            return 'This case exceeds the drafting context limit. No draft was saved.'
        try:
            with tracing.operation('case.draft', {}, kind='agent', private=True):
                plan = (await self.agent.run(payload, usage=usage,
                    usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS),
                    timeout=constants.RUN_TIMEOUT_SECONDS)).output
                if plan.needs_clarification:
                    return 'The recorded facts need clarification before drafting. Please confirm the current refund amount and requested remedy. No draft was saved.'
                content = DraftRenderer().render(current.facts, plan)
        except InvalidDraftPlan:
            return 'Drafting could not finish safely. No draft was saved; your case facts and earlier drafts are unchanged.'
        return await self.store.save_draft(turn, selection, current, content)

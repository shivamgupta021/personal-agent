"""Private model planning with source-locked, immutable factual draft rendering."""

import json
from dataclasses import asdict, replace

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.tracing import tracing
from core.turns import OwnerTurn
from grievances import constants
from grievances.policy_store import PolicyStore, ResearchBriefView, ResearchView
from grievances.progress_store import ProgressSnapshot, ProgressStore
from grievances.prompts import DRAFT_INSTRUCTIONS, FOLLOWUP_INSTRUCTIONS
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


    def with_research(self, content: DraftContent, evidence: ResearchView | None,
                      brief: ResearchBriefView | None, fact_revision: int) -> DraftContent:
        appendix = ''
        cited_revision: int | None = None
        if evidence:
            if brief is None or not brief.confirmed or brief.revision != evidence.brief_revision:
                appendix = ('\n\nPrior research is inactive because the public fields or official site changed. '
                            'Review, confirm and research the current brief before relying on policy evidence.')
            elif evidence.fact_revision != fact_revision:
                appendix = ('\n\nPrior research predates the current case facts. Research again before relying on policy evidence.')
            else:
                cited_revision = evidence.revision
                appendix = '\n\nResearch notes for review (source quotations; applicability is uncertain):\n' + evidence.uncertainty
                if evidence.status == 'verified_sources':
                    for finding in evidence.findings:
                        appendix += (f"\n\n{finding.kind} — {finding.url} (retrieved {finding.retrieved_at}; {finding.applicability}):\n"
                                     + finding.quote)
        return replace(content, body=content.body + appendix, evidence_revision=cited_revision,
                       observed_evidence_revision=evidence.revision if evidence else None,
                       brief_revision=brief.revision if brief else None, brief_confirmed=brief.confirmed if brief else None)


    def with_correspondence(self, content: DraftContent, plan: DraftPlan, progress: ProgressSnapshot) -> DraftContent:
        replies = {reply.id: reply for reply in progress.replies}
        reports = {report.id: report for report in progress.sent}
        references = plan.reply_references
        ids = [reference.reply_id for reference in references]
        if (not references or len(set(ids)) != len(ids) or not progress.replies or progress.replies[-1].id not in ids
                or any(ref.reply_id not in replies or ref.quote not in replies[ref.reply_id].text for ref in references)
                or len(set(plan.sent_report_ids)) != len(plan.sent_report_ids)
                or any(identifier not in reports for identifier in plan.sent_report_ids)):
            raise InvalidDraftPlan('Unverified correspondence reference')
        quoted = '\n\n'.join('In your reply, you wrote: “' + reference.quote + '”' for reference in references)
        opening = ('Thank you for your response. I would like to continue my refund request.\n\n' + quoted)
        if plan.sent_report_ids:
            opening += '\n\nI am following up on my earlier complaint.'
        body = content.body.replace('I am writing to request a refund. Below is my account of the purchase and the problem.',
                                    opening + '\n\nFor clarity, my current account and requested remedy are below.', 1)
        return replace(content, subject='Follow-up — ' + content.subject, body=body, kind='follow_up',
                       reply_ids=tuple(ids), sent_report_ids=tuple(plan.sent_report_ids), progress_revision=progress.revision)


class DraftService:
    def __init__(self, model: Model, store: CaseStore) -> None:
        self.store = store
        self.agent: Agent[None, DraftPlan] = Agent(model, output_type=DraftPlan, instructions=DRAFT_INSTRUCTIONS,
            name='factual_draft', retries=constants.VALIDATION_RETRIES,
            model_settings={'max_tokens': constants.MAX_OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()

    async def run(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, usage: RunUsage, *, follow_up: bool = False) -> str:
        evidence = await PolicyStore().list_research(turn.owner_id, current.id)
        latest = evidence[-1] if evidence else None
        brief = await PolicyStore().latest_brief(turn.owner_id, current.id)
        progress = await ProgressStore().snapshot(turn.owner_id, current.id) if follow_up else None
        if follow_up and (progress is None or not progress.replies):
            return 'Please save the company reply in this case before requesting a follow-up. No draft was saved.'
        payload = json.dumps({'facts': current.facts,
            'available_fact_keys': [key for key in constants.DRAFT_FACT_FIELDS if key in current.facts],
            'correspondence': asdict(progress) if progress else None})
        if len(payload) > constants.MAX_CONTEXT_CHARS:
            return 'This case exceeds the drafting context limit. No draft was saved.'
        try:
            with tracing.operation('case.draft', {}, kind='agent', private=True):
                plan = (await self.agent.run(payload, instructions=FOLLOWUP_INSTRUCTIONS if follow_up else None, usage=usage,
                    usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS),
                    timeout=constants.RUN_TIMEOUT_SECONDS)).output
                if plan.needs_clarification:
                    return 'The recorded facts need clarification before drafting. Please confirm the current refund amount and requested remedy. No draft was saved.'
                content = replace(DraftRenderer().render(current.facts, plan), progress_revision=current.progress_revision)
                if follow_up and progress:
                    content = DraftRenderer().with_correspondence(content, plan, progress)
                elif plan.reply_references or plan.sent_report_ids:
                    raise InvalidDraftPlan('Initial draft cannot invent correspondence')
        except InvalidDraftPlan:
            return 'Drafting could not finish safely. No draft was saved; your case facts and earlier drafts are unchanged.'
        content = DraftRenderer().with_research(content, latest, brief, current.revision)
        return await self.store.save_draft(turn, selection, current, content)

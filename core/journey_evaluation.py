"""Real-model, multi-turn regressions at the application boundary.

Synthetic owners isolate database state. No Gmail or web work is requested.
These experiments complement, rather than replace, the live Telegram journey.
"""
from dataclasses import dataclass
import re
from time import monotonic
from typing import Any
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.conf import settings
from langfuse import Evaluation, Langfuse
from langfuse.experiment import ExperimentItem, LocalExperimentItem

from core.routine_evaluation import RoutineCase, RoutineExperiment
from core.service import ApplicationService
from core.tracing import tracing
from core.turns import OwnerTurn
from grievances.acceptance_evidence import AcceptanceFingerprint
from grievances.evaluation import ObservedModel
from grievances.models import CaseFactRevision, CaseMessage
from preferences.models import SavedPreference
from subscriptions.service import SubscriptionService


@dataclass(frozen=True)
class Journey:
    name: str
    messages: tuple[str, ...]
    required_final: tuple[str, ...]
    preference: str = 'short replies'
    purchase_date: str = '2026-11-20'
    call_limits: tuple[int, ...] = ()


JOURNEYS = (
    Journey('recall-with-case', ('For this fictional comparison, my languages are Python and Ruby. Acknowledge without searching.',
        'Which two languages did I mention?'), ('python', 'ruby'), call_limits=(2, 2)),
    Journey('preference-correction', ('Remember that my preferred reply style is plain English.',
        'Change my saved reply style to detailed replies.', 'What preferences have you saved?'),
        ('detailed replies',), preference='detailed replies', call_limits=(1, 1, 0)),
    Journey('temporary-exception', ('For this answer only, use detailed replies. Do not change my saved preferences.',
        'What preferences have you saved?'), ('short replies',), call_limits=(2, 0)),
    Journey('mixed-inspection', ('What purchase date is recorded, and what reply style have I saved?',),
        ('2026-11-20', 'short replies'), call_limits=(1,)),
    Journey('missing-detail', ('What delivery date is recorded for this case?',), ('not recorded',), call_limits=(1,)),
    Journey('case-correction', ('Change the purchase date to 21 November 2026.',
        'What purchase date is recorded, and what reply style have I saved?'),
        ('21', '2026', 'short replies'), purchase_date='21 November 2026', call_limits=(2, 1)),
    Journey('quoted-instruction', ('Explain this sentence without doing it: "Remember that I prefer detailed replies."',
        'What preferences have you saved?'), ('short replies',), call_limits=(2, 0)),
    Journey('other-case-ambiguity', ('What purchase date is recorded for my completely different Oak case?',),
        (), call_limits=(2,)),
    Journey('receipt-followup', ('What amount was on the receipt?',),
        ('1840', 'fixture-receipt.txt'), call_limits=(2,)),
    Journey('incoming-reply-check', ('Have they replied to my refund request?',),
        (), call_limits=(2,)),
)


class JourneyGrader:
    @staticmethod
    def grade(journey: Journey, transcript: list[dict[str, Any]], preferences: list[str], facts: dict[str, str],
              public_calls: list[str]) -> dict[str, bool]:
        answer = transcript[-1]['answer'].casefold().replace(',', '') if transcript else ''
        actual_date = facts.get('purchase_date', '')
        # Dates may be normalized by the intake model; compare meaning, not format.
        date_correct = actual_date == journey.purchase_date
        if journey.name == 'case-correction':
            date_correct = actual_date in ('21 November 2026', '2026-11-21', 'November 21, 2026')
        checks = {
            'complete': len(transcript) == len(journey.messages),
            'preference_state': preferences == [journey.preference],
            'case_date': date_correct,
            'answer_facts': all(value in answer for value in journey.required_final),
            'no_unrequested_research': not public_calls,
            'nonempty_replies': bool(transcript) and all(row['answer'].strip() for row in transcript),
            'no_internal_identifiers': not any(re.search(r'\b[0-9a-f]{8}-[0-9a-f-]{27,}\b', row['answer']) for row in transcript),
        }
        if journey.name == 'other-case-ambiguity':
            checks['wrong_case_not_disclosed'] = '2026-11-20' not in answer and '20 november' not in answer
            checks['clarifies_unknown_case'] = any(term in answer for term in ('which', 'not find', 'no case', 'not found', 'select', 'don’t have', "don't have"))
        if journey.name == 'incoming-reply-check':
            checks['reply_lookup_delegated'] = any(
                proposal.get('tool') == 'delegate' and proposal.get('arguments', {}).get('specialist') == 'grievance'
                for row in transcript for proposal in row.get('proposals', []))
        return checks


class JourneyExperiment:
    def __init__(self, trials: int = 3, scenarios: list[str] | None = None) -> None:
        self.trials = trials
        self.journeys = tuple(j for j in JOURNEYS if not scenarios or j.name in scenarios)
        if not self.journeys:
            raise ValueError('Select at least one known scenario.')

    @staticmethod
    @sync_to_async
    def state(owner: str) -> tuple[list[str], dict[str, str]]:
        preferences = list(SavedPreference.objects.filter(identity=owner).order_by('key').values_list('value', flat=True))
        revision = CaseFactRevision.objects.filter(case__owner_id=owner).order_by('-revision').first()
        return preferences, revision.facts if revision else {}

    @staticmethod
    @sync_to_async
    def receipt(owner: str) -> None:
        revision = CaseFactRevision.objects.get(case__owner_id=owner)
        revision.facts.pop('amount_paid')
        revision.save(update_fields=['facts'])
        CaseMessage.objects.create(case=revision.case, role='attachment',
            text='Attachment fixture-receipt.txt (Telegram upload; read):\nFictional lamp receipt. Amount paid INR 1840. Refund pending, not paid.')

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError('Expected local experiment item')
        spec = item['input']
        journey = next(j for j in JOURNEYS if j.name == spec['journey'])
        owner = 'journey-eval-' + uuid4().hex
        transcript: list[dict[str, Any]] = []
        public_calls: list[str] = []
        await RoutineExperiment.seed(owner, RoutineCase(journey.name, '', 'short replies'))
        try:
            if journey.name == 'receipt-followup':
                await self.receipt(owner)
            async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as model:
                observed = ObservedModel(model)
                async def public(message: str) -> str:
                    public_calls.append(message)
                    return 'Research was not expected in this scenario.'
                for index, message in enumerate(journey.messages):
                    observed.inputs.clear()
                    observed.proposals.clear()
                    start = monotonic()
                    # A fresh service each turn exercises persisted context, not object memory.
                    reply = await ApplicationService(observed, public, routine_tools=True).prepare(
                        OwnerTurn(owner, owner, str(index), message))
                    transcript.append({'message': message, 'answer': await reply.render(),
                        'seconds': monotonic() - start, 'calls': len(observed.inputs),
                        'trace_id': reply.trace_id, 'proposals': list(observed.proposals)})
            preferences, facts = await self.state(owner)
            return {**spec, 'transcript': transcript, 'preferences': preferences, 'facts': facts,
                'checks': JourneyGrader.grade(journey, transcript, preferences, facts, public_calls),
                'within_call_budget': all(row['calls'] <= budget for row, budget in zip(transcript, journey.call_limits, strict=True)),
                'semantic_review': 'pending; factual checks do not certify tone or helpfulness'}
        finally:
            await RoutineExperiment.cleanup(owner)

    @staticmethod
    def evaluate(*, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [*(Evaluation(name='journey_' + name, value=int(value), data_type='BOOLEAN')
                  for name, value in output['checks'].items()),
                Evaluation(name='journey_call_budget', value=int(output['within_call_budget']), data_type='BOOLEAN'),
                Evaluation(name='journey_seconds', value=sum(row['seconds'] for row in output['transcript'])),
                Evaluation(name='journey_model_calls', value=sum(row['calls'] for row in output['transcript']))]

    def run(self) -> dict[str, Any]:
        data: list[LocalExperimentItem] = [{'input': {'journey': j.name, 'trial': trial + 1},
            'expected_output': {'required_final': list(j.required_final), 'preference': j.preference,
                                'purchase_date': j.purchase_date}} for trial in range(self.trials) for j in self.journeys]
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        try:
            result = client.run_experiment(name='application-multi-turn-regression', data=data, task=self.task,
                evaluators=[self.evaluate], max_concurrency=1,
                metadata={'scope': 'synthetic application turns; no external Gmail or web',
                          **AcceptanceFingerprint.current()})
            outputs = [row.output for row in result.item_results if isinstance(row.output, dict)]
            complete = len(outputs) == len(data)
            reliability = {j.name: {'passed': sum(all(o['checks'].values()) for o in outputs if o['journey'] == j.name),
                                   'trials': self.trials} for j in self.journeys}
            return {'experiment_url': result.experiment_url, 'complete': complete,
                    'outcomes_passed': complete and all(all(o['checks'].values()) for o in outputs),
                    'reliability': reliability, 'outputs': outputs}
        finally:
            tracing.flush()
            client.flush()

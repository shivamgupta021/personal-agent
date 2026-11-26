"""Paired, real-model experiments; synthetic owners never access Gmail or the web."""
from dataclasses import dataclass
import hashlib
import logging
from statistics import median
from time import monotonic
from typing import Any
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.conf import settings
from langfuse import Evaluation, Langfuse
from langfuse.experiment import ExperimentItem, LocalExperimentItem

from conversations.models import ConversationRecord, ConversationTrace
from core.service import ApplicationService
from core.routine_prompts import ROUTINE_INSTRUCTIONS
from core.routing_prompts import ROUTING_INSTRUCTIONS
from core.tracing import tracing
from core.turns import OwnerTurn
from grievances.evaluation import ObservedModel
from grievances.models import (GrievanceCase, CaseFactRevision, CaseSelection,
    CaseOwnerState, CaseOperationReceipt, CaseTrace, CaseTurnRegistration)
from preferences.models import SavedPreference
from subscriptions.constants import DEFAULT_MODEL
from subscriptions.service import SubscriptionService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutineCase:
    name: str
    request: str
    expected_preference: str
    contains: tuple[str, ...] = ()
    selected_case: bool = True
    seed_preference: str = 'short replies'
    targeted: bool = True


CASES = (
    RoutineCase('save', 'Remember that I prefer plain English.', 'plain English', seed_preference=''),
    RoutineCase('change', 'Change my saved reply style to detailed replies.', 'detailed replies'),
    RoutineCase('list', 'Tell me the reply style you have stored for me.', 'short replies', ('short replies',)),
    RoutineCase('case-date', 'What purchase date is recorded for this case?', 'short replies', ('2026-11-20',)),
    RoutineCase('mixed', 'What purchase date is recorded, and what reply style have I saved?', 'short replies', ('2026-11-20', 'short replies')),
    RoutineCase('missing-field', 'What delivery date is recorded for this case?', 'short replies'),
    RoutineCase('temporary', 'For this answer only, use detailed replies. Do not change my saved preferences.', 'short replies', targeted=False),
    RoutineCase('research', 'Find the official Python documentation for asyncio.timeout.', 'short replies', ('Controlled research boundary',), targeted=False),
)


class RoutineExperiment:
    def __init__(self, trials: int = 3) -> None:
        self.trials = trials

    @staticmethod
    @sync_to_async
    def seed(owner: str, case: RoutineCase) -> None:
        if case.selected_case:
            record = GrievanceCase.objects.create(owner_id=owner, label='Fictional lamp refund', label_key='fictional lamp refund', revision=1)
            CaseFactRevision.objects.create(case=record, revision=1, facts={'purchase_date': '2026-11-20', 'amount_paid': 'INR 1840'})
            CaseSelection.objects.create(owner_id=owner, conversation_id=owner, case=record)
        if case.seed_preference:
            SavedPreference.objects.create(identity=owner, key='reply_style', value=case.seed_preference, source_message_id='fixture')

    @staticmethod
    @sync_to_async
    def checks(owner: str, case: RoutineCase, answer: str) -> dict[str, bool]:
        values = list(SavedPreference.objects.filter(identity=owner).values_list('value', flat=True))
        facts = list(CaseFactRevision.objects.filter(case__owner_id=owner).values_list('facts', flat=True))
        checks = {'preference_state': values == [case.expected_preference],
            'case_unchanged': facts == [{'purchase_date': '2026-11-20', 'amount_paid': 'INR 1840'}],
            'answer_facts': all(value.casefold() in answer.casefold() for value in case.contains),
            'nonempty_answer': bool(answer.strip())}
        if case.name == 'missing-field':
            checks['missing_not_invented'] = any(word in answer.lower() for word in ('not recorded', 'no delivery', "don't have", 'not saved', 'missing'))
        if case.name == 'temporary':
            checks['temporary_request_understood'] = '?' not in answer and 'detailed' in answer.lower()
        return checks

    @staticmethod
    @sync_to_async
    def cleanup(owner: str) -> None:
        # Exact per-item UUID identity; never select other application owners.
        GrievanceCase.objects.filter(owner_id=owner).delete()
        for model in (CaseSelection, CaseOwnerState, CaseOperationReceipt, CaseTrace, CaseTurnRegistration):
            model.objects.filter(owner_id=owner).delete()
        for memory in (SavedPreference, ConversationRecord, ConversationTrace):
            memory.objects.filter(identity=owner).delete()

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError('Expected a local experiment item.')
        spec = item['input']
        case = next(case for case in CASES if case.name == spec['case'])
        owner = 'routine-eval-' + uuid4().hex
        await self.seed(owner, case)
        try:
            async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as model:
                observed = ObservedModel(model)
                async def research(message: str) -> str:
                    return 'Controlled research boundary'
                app = ApplicationService(observed, research, routine_tools=spec['variant'] == 'terminal')
                start = monotonic()
                reply = await app.prepare(OwnerTurn(owner, owner, 'trial', case.request))
                answer = await reply.render()
                seconds = monotonic() - start
                logger.info('Routine experiment: trial=%s case=%s variant=%s calls=%s seconds=%.2f',
                    spec['trial'], case.name, spec['variant'], len(observed.inputs), seconds)
                return {**spec, 'request': case.request, 'answer': answer, 'seconds': round(seconds, 3),
                    'model_calls': len(observed.inputs), 'trace_id': reply.trace_id,
                    'proposals': observed.proposals, 'checks': await self.checks(owner, case, answer),
                    'targeted': case.targeted}
        finally:
            await self.cleanup(owner)

    @staticmethod
    def evaluate(*, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [Evaluation(name='routine_outcome_checks', value=int(all(output['checks'].values())), data_type='BOOLEAN',
                comment='Exact stored state and required answer facts. Manual semantic review still required.'),
            Evaluation(name='routine_seconds', value=output['seconds']),
            Evaluation(name='routine_model_calls', value=output['model_calls'])]

    def run(self) -> dict[str, Any]:
        data: list[LocalExperimentItem] = [{'input': {'case': case.name, 'trial': trial + 1, 'variant': variant}}
            for trial in range(self.trials) for index, case in enumerate(CASES)
            for variant in (('baseline', 'terminal') if (trial + index) % 2 == 0 else ('terminal', 'baseline'))]
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
            base_url=settings.LANGFUSE_BASE_URL)
        try:
            result = client.run_experiment(name='routine-tools-paired', data=data, task=self.task,
                evaluators=[self.evaluate], max_concurrency=1,
                metadata={'model': DEFAULT_MODEL, 'synthetic': 'true', 'trials': str(self.trials),
                          'prompt_digest': hashlib.sha256((ROUTING_INSTRUCTIONS + ROUTINE_INSTRUCTIONS).encode()).hexdigest(),
                          'scope': 'terminal preferences and selected-case inspection; no Gmail'})
            outputs = [item.output for item in result.item_results if isinstance(item.output, dict)]
            medians = {variant: median([o['seconds'] for o in outputs if o['variant'] == variant and o['targeted']])
                       for variant in ('baseline', 'terminal')}
            complete = len(outputs) == len(data)
            passed = complete and all(all(o['checks'].values()) for o in outputs)
            by_variant = {variant: all(all(o['checks'].values()) for o in outputs if o['variant'] == variant)
                          for variant in ('baseline', 'terminal')}
            return {'experiment_url': result.experiment_url, 'complete': complete, 'checks_passed': passed,
                'variant_checks_passed': by_variant,
                'candidate_passed': complete and by_variant['terminal'],
                'targeted_median_seconds': medians, 'improvement_fraction': 1 - medians['terminal'] / medians['baseline'],
                'outputs': outputs}
        finally:
            tracing.flush()
            client.flush()

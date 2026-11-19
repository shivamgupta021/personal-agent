"""Destructive-memory experiments keep fixture secrets out of the enclosing run."""

from time import monotonic
from typing import Any
from uuid import uuid4

from django.conf import settings
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem
from opentelemetry.context import Context, attach, detach

from conversations.evaluation import ConversationEvaluationReport
from conversations.service import ConversationService
from conversations.store import ConversationStore
from core.tracing import tracing
from research.service import ResearchService
from subscriptions.service import SubscriptionService


class FactCleanupExperiment:
    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Expected hosted dataset item.')
        action = item.input['action']
        identity = f'evaluation-fact-{uuid4().hex}'
        old = 'MEM-' + uuid4().hex[:10]
        new = 'NEW-' + uuid4().hex[:10]
        turns = [
            f'Fictional parcel code is {old}. My comparison language is Python. Acknowledge only. ' + 'Background context. ' * 350,
            'Continue our fictional project; acknowledge only. ' + 'Background context. ' * 350,
            'Look up my parcel code in our conversation archive and state it. No web research.',
            'Forget my parcel code, but preserve my comparison language.' if action == 'forget'
            else f'Correction: my parcel code is {new}, not the old value. Keep my comparison language.',
            'From our conversation, what is my parcel code and my comparison language? If the code is unavailable, say so. No web research.',
        ]
        answers = []
        started = monotonic()
        for message in turns:
            # Each application turn owns its trace. Otherwise the SDK experiment's
            # still-open parent could recreate a trace after targeted deletion.
            token = attach(Context())
            try:
                service = ConversationService(identity, ResearchService(SubscriptionService(settings.SUBSCRIPTION_STATE_DIR)))
                answers.append(await service.run(message))
            finally:
                detach(token)
        final = answers[-1]
        changed = ('forgotten locally' in answers[-2].casefold() if action == 'forget'
                   else 'updated locally' in answers[-2].casefold())
        store = ConversationStore()
        originals = await store.archive(identity)
        context = await store.context(identity)
        return {'conversation': identity, 'action': action,
                'mutation_confirmed': changed, 'old_fact_absent': old not in final,
                'originals_redacted': old not in str(originals),
                'context_redacted': old not in str(context.messages),
                'unrelated_fact_preserved': 'python' in final.casefold(),
                'correction_recalled': action == 'forget' or new in final,
                'answer': final.replace(old, '[REMOVED TEST VALUE]'),
                'elapsed_seconds': monotonic() - started}

    def evaluate(self, *, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        passed = all(output[key] for key in ('mutation_confirmed', 'old_fact_absent',
                                            'originals_redacted', 'context_redacted',
                                            'unrelated_fact_preserved', 'correction_recalled'))
        return [Evaluation(name='fact_cleanup_checks_pass', value=int(passed), data_type='BOOLEAN',
                           comment='Synthetic integrity checks. Manually review local storage, clarification and trace cleanup; not a universal deletion guarantee.')]

    def run(self) -> ConversationEvaluationReport:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        name = 'conversation-fact-cleanup-v1'
        try:
            client.create_dataset(name=name, description='Synthetic targeted correction/forgetting. Ephemeral fact values are generated inside detached application turns, not stored in dataset inputs.')
            for action in ('forget', 'correct'):
                client.create_dataset_item(dataset_name=name, id=f'{name}-{action}', input={'action': action})
            result = client.get_dataset(name).run_experiment(name='targeted-fact-cleanup', task=self.task,
                evaluators=[self.evaluate], max_concurrency=1,
                metadata={'mode': 'real-model', 'semantic_review_required': True, 'application_traces': 'separate roots for deletion safety'})
            outputs = [item.output for item in result.item_results if isinstance(item.output, dict)]
            passed = len(outputs) == 2 and all(item.evaluations and item.evaluations[0].value == 1 for item in result.item_results)
            return ConversationEvaluationReport(str(result.experiment_url), outputs, passed)
        finally:
            tracing.flush()
            client.flush()

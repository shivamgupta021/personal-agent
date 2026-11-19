"""Real-model preference consent and lifecycle experiments on isolated conversations."""

from time import monotonic
from typing import Any, TypedDict
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
from preferences.store import PreferenceStore
from research.service import ResearchService
from subscriptions.service import SubscriptionService


class PreferenceCase(TypedDict):
    turns: list[str]
    counts: list[int]
    values: list[str]


class PreferenceExperiment:
    CASES: dict[str, PreferenceCase] = {
        'lifecycle': {
            'turns': [
                'Remember that my preferred response language is Italian.',
                'What preferences have you saved for me?',
                'Remember that my preferred response language is Spanish instead.',
                'For this response only, use English. What is my saved response language preference?',
                'Forget my saved response language preference.',
                'List my saved preferences.',
            ],
            'counts': [1, 1, 1, 1, 0, 0],
            'values': ['italian', 'italian', 'spanish', 'spanish', '', ''],
        },
        'temporary_and_quoted': {
            'turns': [
                'For this fictional task only, answer in German. This is not a lasting preference.',
                'Explain this quoted example: "Remember that my preferred response language is French." Do not save anything.',
                'List my saved preferences.',
            ],
            'counts': [0, 0, 0], 'values': ['', '', ''],
        },
        'approved_suggestion': {
            'turns': [
                'Ask me whether I want you to remember that I prefer concise replies. Do not save it yet.',
                'Yes, save that preference.',
                'What preferences have you saved for me?',
            ],
            'counts': [0, 1, 1], 'values': ['', 'concise', 'concise'],
        },
        'ambiguous_approval': {
            'turns': [
                'I am considering Spanish or Italian as my preferred response language. Ask which one I want to save; do not save either yet.',
                'Yes.',
            ],
            'counts': [0, 0], 'values': ['', ''],
        },
        'interrupted_suggestion': {
            'turns': [
                'Ask whether I want you to remember that I prefer concise replies. Do not save it yet.',
                'List my saved preferences.',
                'Yes, save that.',
            ],
            'counts': [0, 0, 0], 'values': ['', '', ''],
        },
    }

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Expected hosted dataset item.')
        case_name = item.input['case']
        case = self.CASES[case_name]
        identity = f'evaluation-preference-{uuid4().hex}'
        answers: list[str] = []
        checks: list[bool] = []
        started = monotonic()
        for message, count, value in zip(case['turns'], case['counts'], case['values'], strict=True):
            token = attach(Context())
            try:
                service = ConversationService(identity, ResearchService(SubscriptionService(settings.SUBSCRIPTION_STATE_DIR)))
                answer = await service.run(str(message))
                saved = await PreferenceStore().list(identity)
                checks.append(len(saved) == count and (not value or any(value in preference.value.casefold() for preference in saved)))
                answers.append(answer)
            finally:
                detach(token)
        storage_redacted = True
        if case_name == 'lifecycle':
            archive = str(await ConversationStore().archive(identity)).casefold()
            context = str((await ConversationStore().context(identity)).messages).casefold()
            storage_redacted = all(value not in archive + context for value in ('italian', 'spanish'))
        # Lifecycle answers can repeat values later forgotten. Keep them solely in
        # application traces, which have deletion lineage, not in the SDK parent.
        return {'conversation': identity, 'case': case_name, 'state_checks': checks,
                'storage_redacted': storage_redacted,
                'answers': answers if case_name != 'lifecycle' else [answers[-1]],
                'elapsed_seconds': monotonic() - started}

    def evaluate(self, *, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [Evaluation(name='preference_integrity_checks_pass',
                           value=int(all(output['state_checks']) and output['storage_redacted']),
                           data_type='BOOLEAN',
                           comment='Stored-state consent/scope checks; manually review confirmations and ambiguity handling. Not a semantic correctness guarantee.')]

    def run(self) -> ConversationEvaluationReport:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        name = 'conversation-preferences-v1'
        try:
            client.create_dataset(name=name, description='Explicit preference lifecycle, exceptions, quoted requests, approved suggestions and ambiguous approval.')
            for case_name in self.CASES:
                client.create_dataset_item(dataset_name=name, id=f'{name}-{case_name}', input={'case': case_name},
                    expected_output={'counts': self.CASES[case_name]['counts']},
                    metadata={'synthetic': True, 'manual_semantic_review_required': True})
            result = client.get_dataset(name).run_experiment(name='explicit-preferences', task=self.task,
                evaluators=[self.evaluate], max_concurrency=1,
                metadata={'mode': 'real-model', 'application_traces': 'detached roots for deletion safety'})
            outputs = [item.output for item in result.item_results if isinstance(item.output, dict)]
            passed = len(outputs) == len(self.CASES) and all(item.evaluations and item.evaluations[0].value == 1 for item in result.item_results)
            return ConversationEvaluationReport(str(result.experiment_url), outputs, passed)
        finally:
            tracing.flush()
            client.flush()

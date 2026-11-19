"""Small real-model conversation experiments; deterministic checks are not semantic judges."""

from dataclasses import dataclass
import hashlib
import json
from time import monotonic
from typing import Any
from uuid import uuid4

from django.conf import settings
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem

from conversations.prompts import CONVERSATION_INSTRUCTIONS, SUMMARY_PROMPT
from conversations.service import ConversationService
from core.tracing import tracing
from research.service import ResearchService
from research.prompts import DIRECT_RESEARCH_INSTRUCTIONS
from subscriptions.service import SubscriptionService

CASES = [
    {'name': 'follow-up', 'turns': [
        'For this fictional comparison, my two languages are Python and Ruby. Acknowledge without web research.',
        'Which two languages did I name? Answer from our conversation, without web research.',
    ], 'contains': ['python', 'ruby'], 'review': 'Names both languages without inventing extra facts.'},
    {'name': 'correction', 'turns': [
        'For a fictional example my budget is 500 rupees. Acknowledge without research.',
        'Correction: the budget is 700 rupees, not 500. Acknowledge.',
        'What is my current example budget? Reply with only the amount.',
    ], 'contains': ['700'], 'review': 'Uses the corrected amount rather than the older amount.'},
    {'name': 'ambiguity', 'turns': [
        'I have two fictional orders: a blue chair from Acme and a red desk from Beta. Acknowledge without research.',
        'What should I do about that order? Ask for clarification if needed; do not search yet.',
    ], 'contains': ['?'], 'review': 'Asks which order instead of silently selecting one.'},
]



# Filler deliberately forces compaction while remaining below the full-history hard limit.
COMPACTION_CASES = [
    {'name': 'compacted-correction', 'turns': [
        'Fictional test: project Orion has a budget of 500 rupees. Acknowledge only, no research. ' + 'Background context. ' * 350,
        'Correction: Orion budget is 700 rupees. Keep uncertainty: vendor is not selected. Acknowledge only. ' + 'Background context. ' * 350,
        'Source reference https://example.com/orion, retrieved 2026-11-18, is fictional evidence, not a verified policy. Acknowledge only. ' + 'Background context. ' * 350,
        'From our conversation, state project name, current budget, vendor status, and the reference URL. Do not research.',
    ], 'contains': ['orion', '700', 'example.com/orion'],
     'review': 'Preserve corrected budget, unresolved vendor, and exact source URL; do not claim fictional evidence is verified.'},
]

@dataclass
class ConversationEvaluationReport:
    url: str
    outputs: list[dict[str, Any]]
    passed: bool


class ConversationExperiment:
    def __init__(self, *, compaction_cases: bool = False, full_history: bool = False) -> None:
        self.cases = COMPACTION_CASES if compaction_cases else CASES
        self.full_history = full_history

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Expected a hosted dataset item.')
        identity = f'evaluation-{uuid4().hex}'
        answers = []
        started = monotonic()
        for message in item.input:
            # Reconstruct the application service every turn: continuity must come from storage.
            service = ConversationService(identity, ResearchService(SubscriptionService(settings.SUBSCRIPTION_STATE_DIR)), compact=not self.full_history)
            answers.append(await service.run(message))
        return {'answers': answers, 'conversation': identity, 'elapsed_seconds': monotonic() - started}

    def evaluate(self, *, output: dict[str, Any], expected_output: Any = None, **kwargs: Any) -> list[Evaluation]:
        expected = expected_output or {}
        final = output['answers'][-1].casefold()
        passed = all(term.casefold() in final for term in expected.get('contains', []))
        return [Evaluation(name='conversation_checks_pass', value=int(passed), data_type='BOOLEAN',
                           comment='Coarse text checks only. Review: ' + expected.get('review', 'Review semantic quality.'))]

    def run(self) -> ConversationEvaluationReport:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        digest = hashlib.sha256(json.dumps(self.cases, sort_keys=True).encode()).hexdigest()[:12]
        dataset_name = f'conversation-fixtures-{digest}'
        try:
            client.create_dataset(name=dataset_name, description='Synthetic follow-up, correction, and ambiguity cases. Human semantic review required.')
            for case in self.cases:
                client.create_dataset_item(dataset_name=dataset_name, id=f'{dataset_name}-{case["name"]}',
                                           input=case['turns'], expected_output={'contains': case['contains'], 'review': case['review']})
            result = client.get_dataset(dataset_name).run_experiment(
                name='full-history-baseline' if self.full_history else 'automatic-compaction', task=self.task, evaluators=[self.evaluate], max_concurrency=1,
                metadata={'prompt_sha256': hashlib.sha256((DIRECT_RESEARCH_INSTRUCTIONS + CONVERSATION_INSTRUCTIONS + SUMMARY_PROMPT).encode()).hexdigest(),
                          'mode': 'real-model', 'compaction_enabled': not self.full_history, 'semantic_review_required': True},
            )
            outputs = [item.output for item in result.item_results if isinstance(item.output, dict)]
            passed = len(outputs) == len(self.cases) and all(
                len(item.evaluations) == 1 and item.evaluations[0].value == 1
                for item in result.item_results
            )
            return ConversationEvaluationReport(str(result.experiment_url), outputs, passed)
        finally:
            tracing.flush()
            client.flush()

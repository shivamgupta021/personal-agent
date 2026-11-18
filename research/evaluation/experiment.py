"""Run research fixtures through Langfuse's dataset experiment runner."""

from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem

from core.tracing import tracing
from research.evaluation.constants import EXPERIMENT_CONCURRENCY
from research.evaluation.results import CaseResult, EvaluationReport
from research.evaluation.runner import ResearchEvaluation
from research.evaluation.schema import EvaluationCase, EvaluationSuite
from subscriptions.service import SubscriptionService


class ResearchScores:
    """Cheap deterministic checks; these scores do not establish semantic correctness."""

    def evaluate(self, *, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        scores = []
        for name, key in [('evidence_checks_pass', 'evidence_checks'), ('answer_checks_pass', 'answer_checks')]:
            checks = output[key]
            failed = [check for check, passed in checks.items() if not passed]
            scores.append(Evaluation(
                name=name, value=int(not failed), data_type='BOOLEAN',
                comment='Failed checks: ' + ', '.join(failed) if failed else 'All deterministic checks passed; manual semantic review required.',
            ))
        scores.append(Evaluation(
            name='research_checks_pass', value=int(output['passed']), data_type='BOOLEAN',
            comment=output.get('error') or 'Combined evidence, answer, and execution checks; not a semantic quality score.',
        ))
        return scores


class ResearchExperiment:
    def __init__(self, model_name: str | None, state_dir: Path) -> None:
        self.model_name = model_name
        self.state_dir = state_dir
        self.evaluator = ResearchEvaluation()

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError("Expected a hosted Langfuse dataset item.")
        case = EvaluationCase.model_validate({
            **item.metadata['fixture'], 'question': item.input, 'expect': item.expected_output,
        })
        suite = EvaluationSuite(version=1, cases=[case])
        if self.model_name:
            async with SubscriptionService(self.state_dir).model(self.model_name) as model:
                report = await self.evaluator.run(suite, model)
        else:
            report = await self.evaluator.run(suite)
        return asdict(report.cases[0])

    def run(self, suite: EvaluationSuite) -> EvaluationReport:
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            raise ValueError('Langfuse project keys are required for experiments.')
        # Its provider emits only SDK experiment spans; native agent spans retain the
        # existing provider. Shared OTel context links them without double-exporting.
        client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
            base_url=settings.LANGFUSE_BASE_URL,
        )
        report = self.evaluator.report(suite, [], self.model_name)
        dataset_name = f'research-fixtures-{report.suite_sha256[:12]}'
        try:
            client.create_dataset(name=dataset_name, description='Four fictional research regression cases; deterministic checks, human review required.')
            for case in suite.cases:
                client.create_dataset_item(
                    dataset_name=dataset_name,
                    id=str(uuid5(NAMESPACE_URL, f'{dataset_name}/{case.id}')),
                    input=case.question, expected_output=case.expect.model_dump(),
                    metadata={'fixture': case.model_dump(exclude={'question', 'expect'}), 'source': 'synthetic regression fixture'},
                )
            dataset = client.get_dataset(dataset_name)
            result = dataset.run_experiment(
                name=f'research-{self.model_name or "replay"}', task=self.task,
                evaluators=[ResearchScores().evaluate], max_concurrency=EXPERIMENT_CONCURRENCY,
                metadata={'mode': report.mode, 'model': report.model, 'suite_sha256': report.suite_sha256,
                          'prompt_sha256': report.prompt_sha256, 'evaluation_version': 'dirty-v1'},
            )
            cases = [CaseResult(**item.output) for item in result.item_results if isinstance(item.output, dict)]
            report = self.evaluator.report(suite, cases, self.model_name)
            report.passed = report.passed and len(cases) == len(suite.cases) and all(
                len(item.evaluations) == 3 for item in result.item_results
            )
            report.experiment_url = result.experiment_url
            report.dataset_name = dataset_name
            return report
        finally:
            tracing.flush()
            client.flush()

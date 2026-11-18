import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from core.commands import run_async
from research.evaluation.runner import ResearchEvaluation
from research.evaluation.schema import EvaluationSuite
from subscriptions.constants import DEFAULT_MODEL
from subscriptions.service import SubscriptionService


class Command(BaseCommand):
    help = 'Evaluate research against fixed web fixtures; replay is deterministic, --live-model uses your subscription.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--suite', type=Path, default=settings.BASE_DIR / 'research/evaluation/cases.json')
        parser.add_argument('--output', type=Path, default=settings.BASE_DIR / '.local/evaluations/latest.json')
        parser.add_argument('--live-model', action='store_true')
        parser.add_argument('--model', default=DEFAULT_MODEL)
        parser.add_argument('--state-dir', type=Path, default=settings.SUBSCRIPTION_STATE_DIR)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            suite = EvaluationSuite.model_validate_json(options['suite'].read_text())
        except (OSError, ValidationError):
            raise CommandError('Could not load a valid evaluation suite.') from None
        report = run_async(self._evaluate(suite, options))
        encoded = json.dumps(report, indent=2)
        output: Path = options['output']
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + '\n')
        self.stdout.write(encoded)
        if not report['passed']:
            raise CommandError('Evaluation checks failed; inspect the report.')

    async def _evaluate(self, suite: EvaluationSuite, options: dict[str, Any]) -> dict[str, Any]:
        evaluator = ResearchEvaluation()
        if options['live_model']:
            async with SubscriptionService(options['state_dir']).model(options['model']) as model:
                return await evaluator.run(suite, model)
        return await evaluator.run(suite)

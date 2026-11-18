import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from core.commands import run_async
from core.errors import user_error_message
from research.evaluation.experiment import ResearchExperiment
from research.evaluation.runner import ResearchEvaluation
from research.evaluation.schema import EvaluationSuite
from subscriptions.constants import DEFAULT_MODEL


class Command(BaseCommand):
    help = 'Evaluate research against fixed web fixtures; replay is deterministic, --live-model uses your subscription.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--suite', type=Path, default=settings.BASE_DIR / 'research/evaluation/cases.json')
        parser.add_argument('--output', type=Path, default=settings.BASE_DIR / '.local/evaluations/latest.json')
        parser.add_argument('--live-model', action='store_true', help='Run a real-model Langfuse experiment.')
        parser.add_argument('--langfuse', action='store_true', help='Publish a controlled replay experiment to Langfuse.')
        parser.add_argument('--model', default=DEFAULT_MODEL)
        parser.add_argument('--state-dir', type=Path, default=settings.SUBSCRIPTION_STATE_DIR)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            suite = EvaluationSuite.model_validate_json(options['suite'].read_text())
        except (OSError, ValidationError):
            raise CommandError('Could not load a valid evaluation suite.') from None
        if options['live_model'] or options['langfuse']:
            if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
                raise CommandError('Configure both Langfuse project keys in .env before running an experiment.')
            try:
                report = ResearchExperiment(
                    options['model'] if options['live_model'] else None, options['state_dir'],
                ).run(suite)
            except Exception as error:
                raise CommandError(user_error_message(error)) from None
        else:
            report = run_async(ResearchEvaluation().run(suite))
        encoded = json.dumps(asdict(report), indent=2)
        output: Path = options['output']
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + '\n')
        self.stdout.write(encoded)
        if not report.passed:
            raise CommandError('Evaluation checks failed; inspect the report.')

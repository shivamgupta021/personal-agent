"""Compare a terminal routine tool with the current router/specialist pipeline."""
import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.errors import user_error_message
from core.routine_evaluation import RoutineExperiment


class Command(BaseCommand):
    help = 'Run paired real-model routine experiments in Langfuse using fictional local state.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--trials', type=int, default=3)

    def handle(self, *args: Any, **options: Any) -> None:
        if options['trials'] < 1:
            raise CommandError('Use at least one trial.')
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            raise CommandError('Configure both Langfuse project keys first.')
        try:
            report = RoutineExperiment(options['trials']).run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(report, indent=2))
        if not report['candidate_passed']:
            raise CommandError('Candidate outcome checks failed; review the experiment before enabling it.')

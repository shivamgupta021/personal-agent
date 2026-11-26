"""Run detailed multi-turn model regressions with independent stored-state checks."""
import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.errors import user_error_message
from core.journey_evaluation import JOURNEYS, JourneyExperiment


class Command(BaseCommand):
    help = 'Run synthetic multi-turn application experiments in Langfuse.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--trials', type=int, default=3)
        parser.add_argument('--scenario', action='append', choices=[j.name for j in JOURNEYS])

    def handle(self, *args: Any, **options: Any) -> None:
        if options['trials'] < 1:
            raise CommandError('Use at least one trial.')
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            raise CommandError('Configure Langfuse project keys first.')
        try:
            report = JourneyExperiment(options['trials'], options['scenario']).run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(report, indent=2))
        if not report['outcomes_passed']:
            raise CommandError('Journey outcome checks failed; inspect the experiment.')

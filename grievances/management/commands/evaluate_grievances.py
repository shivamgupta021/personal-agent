"""Run synthetic grievance experiments through Langfuse."""

from dataclasses import asdict
import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.errors import user_error_message
from grievances.evaluation import IntakeExperiment
from grievances.draft_evaluation import DraftExperiment
from grievances.policy_evaluation import PolicyExperiment


class Command(BaseCommand):
    help = 'Run real-model synthetic grievance experiments; no external actions.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--scenario', choices=PolicyExperiment.CASES, help='Run one policy dataset item; requires --policy.')
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--policy', action='store_true', help='Evaluate official policy evidence with fixed HTTP sources.')
        group.add_argument('--drafts', action='store_true', help='Evaluate factual draft versions instead of intake.')

    def handle(self, *args: Any, **options: Any) -> None:
        if options['scenario'] and not options['policy']:
            raise CommandError('--scenario requires --policy.')
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            raise CommandError('Configure both Langfuse project keys first.')
        try:
            experiment = PolicyExperiment(options['scenario']) if options['policy'] else DraftExperiment() if options['drafts'] else IntakeExperiment()
            report = experiment.run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(asdict(report), indent=2))
        if not report.passed:
            raise CommandError('Grievance checks failed; review the experiment outputs.')

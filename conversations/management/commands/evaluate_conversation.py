from typing import Any
from dataclasses import asdict
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from conversations.evaluation import ConversationExperiment
from conversations.fact_evaluation import FactCleanupExperiment
from conversations.preference_evaluation import PreferenceExperiment
from core.errors import user_error_message


class Command(BaseCommand):
    help = 'Run synthetic multi-turn Langfuse experiments using the real subscription model.'

    def add_arguments(self, parser: CommandParser) -> None:
        cases = parser.add_mutually_exclusive_group()
        cases.add_argument('--preferences', action='store_true', help='Use real-model preference consent and lifecycle cases.')
        cases.add_argument('--facts', action='store_true', help='Use real-model targeted correction and forgetting cases.')
        cases.add_argument('--recall', action='store_true', help='Use archive recall, correction, ambiguity and no-match cases.')
        cases.add_argument('--compaction', action='store_true', help='Use the long-context compaction cases.')
        parser.add_argument('--full-history', action='store_true', help='Disable compaction for a bounded comparison baseline.')

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            raise CommandError('Configure both Langfuse project keys first.')
        try:
            report = (PreferenceExperiment().run() if options['preferences'] else
                      FactCleanupExperiment().run() if options['facts'] else
                      ConversationExperiment(compaction_cases=options['compaction'], full_history=options['full_history'], recall_cases=options['recall']).run())
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(asdict(report), indent=2))
        if not report.passed:
            raise CommandError('Conversation checks failed; review the experiment outputs.')

from typing import Any
from dataclasses import asdict
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from conversations.evaluation import ConversationExperiment
from core.errors import user_error_message


class Command(BaseCommand):
    help = 'Run synthetic multi-turn Langfuse experiments using the real subscription model.'

    def add_arguments(self, parser: CommandParser) -> None:
        cases = parser.add_mutually_exclusive_group()
        cases.add_argument('--recall', action='store_true', help='Use archive recall, correction, ambiguity and no-match cases.')
        cases.add_argument('--compaction', action='store_true', help='Use the long-context compaction cases.')
        parser.add_argument('--full-history', action='store_true', help='Disable compaction for a bounded comparison baseline.')

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            raise CommandError('Configure both Langfuse project keys first.')
        try:
            report = ConversationExperiment(compaction_cases=options['compaction'], full_history=options['full_history'], recall_cases=options['recall']).run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(asdict(report), indent=2))
        if not report.passed:
            raise CommandError('Conversation checks failed; review the experiment outputs.')

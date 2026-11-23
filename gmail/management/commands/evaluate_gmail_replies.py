"""Run the synthetic Gmail reply-check dataset with the configured real model."""
import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from core.errors import user_error_message
from gmail.reply_evaluation import GmailReplyExperiment


class Command(BaseCommand):
    help = 'Evaluate natural reply checking in Langfuse using only synthetic Gmail HTTP.'

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            report = GmailReplyExperiment().run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(report, indent=2))
        if not report['passed']:
            raise CommandError('Reply evaluation failed; inspect the experiment.')

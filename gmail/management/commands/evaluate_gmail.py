"""Rerunnable synthetic Gmail experiment; does not access the owner's mailbox."""
import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from core.errors import user_error_message
from gmail.evaluation import GmailExperiment


class Command(BaseCommand):
    help = 'Run real-model Gmail routing with synthetic email in Langfuse.'

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            report = GmailExperiment().run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(report, indent=2))
        if not report['passed']:
            raise CommandError('Gmail evaluation failed; inspect the experiment.')

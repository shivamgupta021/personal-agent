"""Run the synthetic Gmail reminder dataset with the configured real model."""
import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from core.errors import user_error_message
from gmail.reminder_evaluation import GmailReminderExperiment


class Command(BaseCommand):
    help = 'Evaluate natural reminders in Langfuse using only synthetic Gmail HTTP.'

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            report = GmailReminderExperiment().run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(report, indent=2))
        if not report['passed']:
            raise CommandError('Reminder evaluation failed; inspect the experiment.')

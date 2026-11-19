"""Run synthetic grievance experiments through Langfuse."""

from dataclasses import asdict
import json
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.errors import user_error_message
from grievances.evaluation import IntakeExperiment


class Command(BaseCommand):
    help = 'Run real-model synthetic grievance intake experiments; no external actions.'

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            raise CommandError('Configure both Langfuse project keys first.')
        try:
            report = IntakeExperiment().run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(asdict(report), indent=2))
        if not report.passed:
            raise CommandError('Case intake checks failed; review the experiment outputs.')

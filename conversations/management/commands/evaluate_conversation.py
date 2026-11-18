from typing import Any
from dataclasses import asdict
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from conversations.evaluation import ConversationExperiment
from core.errors import user_error_message


class Command(BaseCommand):
    help = 'Run synthetic multi-turn Langfuse experiments using the real subscription model.'

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
            raise CommandError('Configure both Langfuse project keys first.')
        try:
            report = ConversationExperiment().run()
        except Exception as error:
            raise CommandError(user_error_message(error)) from None
        self.stdout.write(json.dumps(asdict(report), indent=2))
        if not report.passed:
            raise CommandError('Conversation checks failed; review the experiment outputs.')

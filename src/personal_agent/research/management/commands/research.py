from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from personal_agent.core.commands import run_async
from personal_agent.research.constants import MAX_MESSAGE_CHARACTERS
from personal_agent.research.services import ResearchService
from personal_agent.subscriptions.services import SubscriptionService


class Command(BaseCommand):
    help = 'Research a public question using your subscription and local Firecrawl.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('message')
        parser.add_argument('--model', required=True)
        parser.add_argument('--state-dir', type=Path, default=settings.SUBSCRIPTION_STATE_DIR)

    def handle(self, *args: Any, **options: Any) -> str:
        message = options['message'].strip()
        if not message or len(message) > MAX_MESSAGE_CHARACTERS:
            raise CommandError(f'Provide a public question of 1–{MAX_MESSAGE_CHARACTERS} characters.')
        service = ResearchService(SubscriptionService(options['state_dir']))
        return run_async(service.run(message, options['model']))

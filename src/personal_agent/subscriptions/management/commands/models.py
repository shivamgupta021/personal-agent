from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from personal_agent.core.commands import run_async
from personal_agent.subscriptions.services import SubscriptionService


class Command(BaseCommand):
    help = 'List models available through your ChatGPT subscription.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--state-dir', type=Path, default=settings.SUBSCRIPTION_STATE_DIR)

    def handle(self, *args: Any, **options: Any) -> str:
        models = run_async(SubscriptionService(options['state_dir']).list_models())
        return '\n'.join(models)

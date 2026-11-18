from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from core.commands import run_async
from subscriptions.service import SubscriptionService


class Command(BaseCommand):
    help = 'Sign in with your ChatGPT subscription.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--state-dir', type=Path, default=settings.SUBSCRIPTION_STATE_DIR)

    def handle(self, *args: Any, **options: Any) -> None:
        run_async(SubscriptionService(options['state_dir']).login())

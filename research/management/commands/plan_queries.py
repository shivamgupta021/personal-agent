from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.commands import run_async
from research.constants import MAX_MESSAGE_CHARACTERS
from research.planning.agent import PlanningMode
from research.planning.service import PlanningService
from subscriptions.constants import DEFAULT_MODEL
from subscriptions.service import SubscriptionService


class Command(BaseCommand):
    help = 'Plan public-web queries without executing searches or reading pages.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('message')
        parser.add_argument('--mode', choices=list(PlanningMode), default=PlanningMode.SIMPLE)
        parser.add_argument('--model', default=DEFAULT_MODEL, help=f'Model slug (default: {DEFAULT_MODEL}).')
        parser.add_argument('--state-dir', type=Path, default=settings.SUBSCRIPTION_STATE_DIR)

    def handle(self, *args: Any, **options: Any) -> str:
        message = options['message'].strip()
        if not message or len(message) > MAX_MESSAGE_CHARACTERS:
            raise CommandError(f'Provide a public question of 1–{MAX_MESSAGE_CHARACTERS} characters.')
        service = PlanningService(SubscriptionService(options['state_dir']))
        return run_async(service.run(message, options['model'], PlanningMode(options['mode']))).model_dump_json(indent=2)

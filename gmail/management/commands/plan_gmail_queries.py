"""Inspect the independent Gmail query planner without any mailbox access."""
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.commands import run_async
from core.email_telemetry import email_private
from gmail.planner import GmailQueryPlanner
from grievances.constants import MAX_INPUT_CHARS
from subscriptions.service import SubscriptionService


class Command(BaseCommand):
    help = 'Generate Gmail API search queries only; no email is fetched.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('request')
        parser.add_argument('--case-label', default='')
        parser.add_argument('--company', default='')
        parser.add_argument('--order-reference', default='')

    def handle(self, *args: Any, **options: Any) -> str:
        request = options['request'].strip()
        identifiers = {key: options[key] for key in ('company', 'order_reference') if options[key]}
        label = options['case_label']
        if not request or len(request) + len(label) + sum(map(len, identifiers.values())) > MAX_INPUT_CHARS:
            raise CommandError('Provide a nonempty, bounded search request and case details.')
        return run_async(self.plan(request, label, identifiers))

    async def plan(self, request: str, label: str, identifiers: dict[str, str]) -> str:
        token = email_private.set(True)
        try:
            async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as model:
                result = await GmailQueryPlanner(model).run(request, label, identifiers)
                return result.model_dump_json(indent=2)
        finally:
            email_private.reset(token)

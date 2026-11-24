"""Run the local owner-only Telegram research bot."""

import hashlib
import re
from typing import Any

import httpx2
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.commands import run_async
from gmail.factory import GmailFactory
from gmail.monitoring import MonitoringWorker
from conversations.service import ConversationService
from research.service import ResearchService
from grievances.service import SubscriptionApplicationService
from subscriptions.service import SubscriptionService
from telegram_bot.client import TelegramClient
from telegram_bot.constants import HTTP_TIMEOUT_SECONDS
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState


class Command(BaseCommand):
    help = 'Run owner-only Telegram research locally; interrupted requests require manual resend.'

    def handle(self, *args: Any, **options: Any) -> None:
        token = settings.TELEGRAM_BOT_TOKEN
        try:
            owner_id = int(settings.TELEGRAM_OWNER_ID)
        except (ValueError, TypeError):
            owner_id = 0
        if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token) or owner_id <= 0:
            raise CommandError('Set TELEGRAM_BOT_TOKEN and a positive numeric TELEGRAM_OWNER_ID in .env.')
        run_async(self.run_bot(token, owner_id))

    async def run_bot(self, token: str, owner_id: int) -> None:
        # Separate cursor/lock per bot without putting its secret in a filename.
        identity = hashlib.sha256(token.split(':', 1)[0].encode()).hexdigest()[:16]
        state = PollingState(settings.TELEGRAM_STATE_DIR / identity)
        subscription = SubscriptionService(settings.SUBSCRIPTION_STATE_DIR)
        research = ResearchService(subscription)
        owner_identity = f'{identity}:{owner_id}'
        application = SubscriptionApplicationService(subscription, ConversationService(owner_identity, research).run)
        async with httpx2.AsyncClient(
            base_url=f'https://api.telegram.org/bot{token}/', timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            telegram = TelegramClient(client)
            monitor = MonitoringWorker(subscription.model, GmailFactory.mailbox,
                lambda reply: telegram.send(owner_id, reply))
            await TelegramService(telegram, state, owner_id, turn_handler=application.prepare,
                identity=owner_identity, background_tick=lambda: monitor.tick(owner_identity)).run()

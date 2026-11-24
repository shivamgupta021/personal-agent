"""Run the local owner-only Telegram research bot."""

import asyncio
import signal
from argparse import ArgumentParser
from typing import Any

import httpx2
from django.conf import settings
from django.core.management.base import BaseCommand

from core.commands import run_async
from gmail.factory import GmailFactory
from gmail.monitoring import MonitoringWorker
from gmail.scheduler import MonitoringScheduler
from telegram_bot.runtime import ApplicationRuntime
from conversations.service import ConversationService
from research.service import ResearchService
from grievances.service import SubscriptionApplicationService
from subscriptions.service import SubscriptionService
from telegram_bot.configuration import TelegramConfiguration
from telegram_bot.client import TelegramClient
from telegram_bot.constants import HTTP_TIMEOUT_SECONDS
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState


class Command(BaseCommand):
    help = 'Run owner-only Telegram and scheduled Gmail monitoring together.'

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument('--chat-only', action='store_true', help='Disable bundled monitoring for a separately managed worker.')

    def handle(self, *args: Any, **options: Any) -> None:
        configuration = TelegramConfiguration.load()
        run_async(self.run_bot(configuration, chat_only=options['chat_only']))

    async def run_bot(self, configuration: TelegramConfiguration, *, chat_only: bool) -> None:
        token, owner_id = configuration.token, configuration.owner_id
        state = PollingState(settings.TELEGRAM_STATE_DIR / configuration.bot_identity)
        subscription = SubscriptionService(settings.SUBSCRIPTION_STATE_DIR)
        research = ResearchService(subscription)
        owner_identity = configuration.owner_identity
        application = SubscriptionApplicationService(subscription, ConversationService(owner_identity, research).run)
        async with httpx2.AsyncClient(
            base_url=f'https://api.telegram.org/bot{token}/', timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            telegram = TelegramClient(client, trace_base_url=settings.TELEGRAM_TRACE_BASE_URL)
            chat = TelegramService(telegram, state, owner_id, turn_handler=application.prepare,
                identity=owner_identity)
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, stop.set)
            try:
                if chat_only:
                    await ApplicationRuntime(chat.run).run(stop)
                else:
                    worker = MonitoringWorker(subscription.model, GmailFactory.mailbox,
                        lambda reply: telegram.send(owner_id, reply))
                    scheduler = MonitoringScheduler(
                        settings.TELEGRAM_STATE_DIR / configuration.bot_identity / 'monitor',
                        lambda: worker.tick(owner_identity))
                    await ApplicationRuntime(chat.run, lambda: scheduler.run(stop)).run(stop)
            finally:
                loop.remove_signal_handler(signal.SIGTERM)

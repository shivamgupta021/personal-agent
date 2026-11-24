"""Run selected-correspondence checks without a Telegram polling process."""

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
from subscriptions.service import SubscriptionService
from telegram_bot.client import TelegramClient
from telegram_bot.configuration import TelegramConfiguration
from telegram_bot.constants import HTTP_TIMEOUT_SECONDS


class Command(BaseCommand):
    help = 'Run local Gmail monitoring and reminder delivery independently of Telegram polling.'

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument('--once', action='store_true', help='Process one bounded tick and exit.')

    def handle(self, *args: Any, **options: Any) -> None:
        configuration = TelegramConfiguration.load()
        run_async(self.run_worker(configuration, once=options['once']))

    async def run_worker(self, configuration: TelegramConfiguration, *, once: bool) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, stop.set)
        subscription = SubscriptionService(settings.SUBSCRIPTION_STATE_DIR)
        try:
            async with httpx2.AsyncClient(
                base_url=f'https://api.telegram.org/bot{configuration.token}/',
                timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False,
            ) as client:
                telegram = TelegramClient(client)
                worker = MonitoringWorker(subscription.model, GmailFactory.mailbox,
                    lambda reply: telegram.send(configuration.owner_id, reply))
                scheduler = MonitoringScheduler(
                    settings.TELEGRAM_STATE_DIR / configuration.bot_identity / 'monitor',
                    lambda: worker.tick(configuration.owner_identity))
                await scheduler.run(stop, once=once)
        finally:
            loop.remove_signal_handler(signal.SIGTERM)

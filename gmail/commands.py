"""Shared construction and sanitized command boundary."""

import hashlib
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from gmail.client import GoogleClient
from gmail.credentials import KeychainStore
from gmail.errors import GmailError
from gmail.service import GmailService


class GmailCommand(BaseCommand):
    def service(self) -> GmailService:
        owner = str(settings.TELEGRAM_OWNER_ID)
        if not owner.isdigit():
            raise CommandError('Configure TELEGRAM_OWNER_ID before connecting Gmail.')
        directory = Path(settings.GMAIL_STATE_DIR) / owner
        account = hashlib.sha256(str(settings.BASE_DIR).encode()).hexdigest()[:16] + ':' + owner
        return GmailService(KeychainStore(directory, account), GoogleClient(), directory)

    def handle(self, *args: Any, **options: Any) -> str:
        try:
            return self.execute_operation(self.service(), options)
        except GmailError as error:
            raise CommandError(str(error)) from None

    def execute_operation(self, service: GmailService, options: dict[str, Any]) -> str:
        raise NotImplementedError

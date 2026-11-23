"""Construct the configured owner's Keychain-backed Gmail connection."""
from contextlib import contextmanager
from collections.abc import Iterator
import httpx2

import hashlib
from pathlib import Path

from django.conf import settings

from gmail.client import GoogleClient
from gmail.credentials import KeychainStore
from gmail.errors import GmailError
from gmail.service import GmailService
from gmail.mailbox import Mailbox
from gmail.constants import HTTP_TIMEOUT_SECONDS


class GmailFactory:
    @staticmethod
    def connection() -> GmailService:
        owner = str(settings.TELEGRAM_OWNER_ID)
        if not owner.isdigit():
            raise GmailError('Configure TELEGRAM_OWNER_ID before connecting Gmail.')
        directory = Path(settings.GMAIL_STATE_DIR) / owner
        account = hashlib.sha256(str(settings.BASE_DIR).encode()).hexdigest()[:16] + ':' + owner
        return GmailService(KeychainStore(directory, account), GoogleClient(), directory)

    @staticmethod
    @contextmanager
    def mailbox() -> Iterator[Mailbox]:
        with httpx2.Client(timeout=HTTP_TIMEOUT_SECONDS) as http:
            yield Mailbox(GmailFactory.connection(), http)

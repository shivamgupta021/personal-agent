"""Shared validated Telegram account identity for polling and notifications."""

import hashlib
import re
from dataclasses import dataclass

from django.conf import settings
from django.core.management.base import CommandError


@dataclass(frozen=True)
class TelegramConfiguration:
    token: str
    owner_id: int
    bot_identity: str

    @property
    def owner_identity(self) -> str:
        return f'{self.bot_identity}:{self.owner_id}'

    @classmethod
    def load(cls) -> 'TelegramConfiguration':
        token = settings.TELEGRAM_BOT_TOKEN
        try:
            owner_id = int(settings.TELEGRAM_OWNER_ID)
        except (ValueError, TypeError):
            owner_id = 0
        if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token) or owner_id <= 0:
            raise CommandError('Set TELEGRAM_BOT_TOKEN and a positive numeric TELEGRAM_OWNER_ID in .env.')
        identity = hashlib.sha256(token.split(':', 1)[0].encode()).hexdigest()[:16]
        return cls(token, owner_id, identity)

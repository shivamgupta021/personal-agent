"""Desktop consent initiated by an owner request, without terminal commands."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from core.tracing import tracing
from core.turns import OwnerTurn
from gmail.errors import GmailError
from gmail.service import GmailService


class GmailConnectionRequested(Exception):
    def __init__(self, email: str, compose: bool) -> None:
        self.email = email
        self.compose = compose
        super().__init__('Owner requested Google consent')


@dataclass(frozen=True)
class ConnectionAttempt:
    connected: bool
    message: str


class GmailOnboarding:
    def __init__(self, service: Callable[[], GmailService], client_file: Path,
                 progress: Callable[[str], Awaitable[None]]) -> None:
        self.service = service
        self.client_file = client_file
        self.progress = progress

    async def connect(self, turn: OwnerTurn, email: str, compose: bool) -> ConnectionAttempt:
        if not self.client_file.is_file():
            return ConnectionAttempt(False, 'Google connection setup is unavailable on this Mac. The operator needs to configure the Google OAuth client file first.')
        capability = 'read email and create unsent drafts' if compose else 'read email'
        await self.progress(f'I’m opening Google consent on this Mac to {capability} for {email}. '
            'Choose that account and approve access in the browser. I’ll continue here when it finishes. '
            'This local consent flow cannot finish in a phone browser.')
        with tracing.operation('gmail.connect', {'compose': compose}, private=True) as span:
            try:
                result = await asyncio.to_thread(self.service().connect,
                    self.client_file, email, compose=compose)
            except GmailError:
                tracing.record_output(span, {'connected': False})
                return ConnectionAttempt(False, 'Gmail connection did not complete. Consent may have been cancelled, timed out, or used a different account. Your previous connection is preserved. Ask me to connect Gmail again when ready.')
            tracing.record_output(span, {'connected': True, 'compose': compose})
            return ConnectionAttempt(True, result)

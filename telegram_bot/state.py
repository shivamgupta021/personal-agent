"""Exclusive local poller state; only the next update ID is retained."""

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import time

from telegram_bot.constants import CURSOR_EXPIRY_SECONDS


class PollingState:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / 'cursor.json'

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.directory / 'poller.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another Telegram poller is using this state directory.') from None
            yield
        finally:
            os.close(descriptor)

    def offset(self) -> int:
        if not self.path.exists():
            return 0
        record = json.loads(self.path.read_text())
        if time() - record['saved_at'] >= CURSOR_EXPIRY_SECONDS:
            return 0  # Telegram may choose a random update ID after a week without updates.
        offset = record['offset']
        if type(offset) is not int or offset < 0:
            raise ValueError('Invalid Telegram cursor; restore state before restarting.')
        return offset

    def advance(self, offset: int) -> None:
        descriptor, temporary = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(descriptor, 'w') as file:
                json.dump({'offset': offset, 'saved_at': time()}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

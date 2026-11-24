"""Single-host monitoring scheduler, independent of Telegram request processing."""

import asyncio
import fcntl
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from django.utils import timezone

from gmail.monitoring_constants import SCHEDULER_TICK_SECONDS

logger = logging.getLogger(__name__)


class MonitoringScheduler:
    def __init__(self, directory: Path, tick: Callable[[], Awaitable[None]]) -> None:
        self.directory = directory
        self.tick = tick

    def heartbeat(self, status: str) -> None:
        temporary = self.directory / 'heartbeat.tmp'
        temporary.write_text(json.dumps({'pid': os.getpid(), 'status': status,
            'updated_at': timezone.now().isoformat()}))
        temporary.replace(self.directory / 'heartbeat.json')

    async def run(self, stop: asyncio.Event, *, once: bool = False) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.directory / 'worker.lock', os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another monitoring worker is using this state directory.') from None
            acquired = True
            logger.info('Monitoring scheduler started')
            while not stop.is_set():
                self.heartbeat('checking')
                try:
                    await self.tick()
                except Exception:
                    self.heartbeat('failed')
                    logger.warning('Monitoring tick failed; retrying after scheduler interval')
                    if once:
                        raise
                else:
                    self.heartbeat('waiting')
                if once:
                    return
                try:
                    await asyncio.wait_for(stop.wait(), timeout=SCHEDULER_TICK_SECONDS)
                except TimeoutError:
                    pass
        finally:
            try:
                if acquired:
                    self.heartbeat('stopped')
                    logger.info('Monitoring scheduler stopped')
            finally:
                os.close(descriptor)

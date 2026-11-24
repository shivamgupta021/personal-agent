"""Public scheduler lifecycle checks; no real mailbox or Telegram access."""

import asyncio
import json
from pathlib import Path

import pytest

from gmail.scheduler import MonitoringScheduler


def test_exclusive_worker_and_release_after_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()

        async def tick() -> None:
            entered.set()
            await asyncio.Event().wait()

        first = asyncio.create_task(MonitoringScheduler(tmp_path, tick).run(asyncio.Event()))
        await entered.wait()
        with pytest.raises(RuntimeError, match='Another monitoring worker'):
            await MonitoringScheduler(tmp_path, tick).run(asyncio.Event(), once=True)
        assert json.loads((tmp_path / 'heartbeat.json').read_text())['status'] == 'checking'
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        calls = []

        async def replacement() -> None:
            calls.append('checked')

        await MonitoringScheduler(tmp_path, replacement).run(asyncio.Event(), once=True)
        assert calls == ['checked']
        assert json.loads((tmp_path / 'heartbeat.json').read_text())['status'] == 'stopped'

    asyncio.run(scenario())


def test_failure_releases_worker_and_once_reports_failure(tmp_path: Path) -> None:
    async def fail() -> None:
        raise ValueError('controlled failure')

    for _ in range(2):
        with pytest.raises(ValueError, match='controlled failure'):
            asyncio.run(MonitoringScheduler(tmp_path, fail).run(asyncio.Event(), once=True))


def test_graceful_stop_finishes_current_tick(tmp_path: Path) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        completed = []

        async def tick() -> None:
            stop.set()
            await asyncio.sleep(0)
            completed.append(True)

        await MonitoringScheduler(tmp_path, tick).run(stop)
        assert completed == [True]
        assert json.loads((tmp_path / 'heartbeat.json').read_text())['status'] == 'stopped'

    asyncio.run(scenario())


def test_failed_tick_can_recover_without_restarting_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('gmail.scheduler.SCHEDULER_TICK_SECONDS', 0.001)

    async def scenario() -> None:
        stop = asyncio.Event()
        calls = []

        async def tick() -> None:
            calls.append(True)
            if len(calls) == 1:
                raise ValueError('controlled failure')
            stop.set()

        await MonitoringScheduler(tmp_path, tick).run(stop)
        assert len(calls) == 2

    asyncio.run(scenario())

"""Single-command lifetime and independent progress through public runtime methods."""

import asyncio

import pytest

from telegram_bot.runtime import ApplicationRuntime


def test_monitor_progresses_while_chat_is_waiting_and_both_stop() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        chat_entered = asyncio.Event()
        closed: set[str] = set()

        async def chat() -> None:
            try:
                chat_entered.set()
                await asyncio.Event().wait()
            finally:
                closed.add('chat')

        async def monitor() -> None:
            try:
                await chat_entered.wait()
                stop.set()
                await asyncio.Event().wait()
            finally:
                closed.add('monitor')

        await ApplicationRuntime(chat, monitor).run(stop)
        assert closed == {'chat', 'monitor'}

    asyncio.run(scenario())


@pytest.mark.parametrize('fails', [True, False])
def test_worker_exit_stops_sibling_and_is_reported(fails: bool) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def chat() -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def monitor() -> None:
            await entered.wait()
            if fails:
                raise ValueError('controlled worker failure')

        with pytest.raises((ValueError, RuntimeError)):
            await ApplicationRuntime(chat, monitor).run(asyncio.Event())
        assert closed.is_set()

    asyncio.run(scenario())

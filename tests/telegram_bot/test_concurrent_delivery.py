"""Concurrent notifications cannot split a long reply in bundled mode."""

import asyncio

import httpx2
import pytest

from telegram_bot.client import TelegramClient


def test_concurrent_sends_preserve_whole_message_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('telegram_bot.client.MESSAGE_MAX_UTF16_UNITS', 3)
    monkeypatch.setattr('telegram_bot.client.MESSAGE_INTERVAL_SECONDS', 0)

    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        sent: list[str] = []

        async def respond(request: httpx2.Request) -> httpx2.Response:
            if not sent:
                entered.set()
                await release.wait()
            sent.append(request.content.decode())
            return httpx2.Response(200, json={'ok': True, 'result': {}})

        async with httpx2.AsyncClient(base_url='https://telegram.invalid/',
                transport=httpx2.MockTransport(respond)) as http:
            client = TelegramClient(http)
            first = asyncio.create_task(client.send(1, 'abcdef'))
            await entered.wait()
            second = asyncio.create_task(client.send(1, 'xyz'))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(first, second)
        assert len(sent) == 3
        assert 'abc' in sent[0] and 'def' in sent[1] and 'xyz' in sent[2]

    asyncio.run(scenario())

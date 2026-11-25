"""Gmail review actions survive Telegram delivery formatting and authorization."""
import asyncio
import json
from typing import Any

import httpx2
import pytest

from core.turns import PreparedReply
from telegram_bot.client import TelegramClient


@pytest.mark.parametrize('long,valid', [(False, True), (True, True), (False, False)])
def test_draft_button_preserves_trace_and_delivery_guard(long: bool, valid: bool) -> None:
    async def scenario() -> None:
        sent: list[dict[str, Any]] = []
        url = 'https://mail.google.com/mail/?authuser=owner%40example.com#inbox?compose=abc123'

        def respond(request: httpx2.Request) -> httpx2.Response:
            sent.append(json.loads(request.content))
            return httpx2.Response(200, json={'ok': True, 'result': {}})

        async def allowed() -> bool:
            return valid

        text = ('Review notes. ' * 400 if long else 'Draft v1 is ready for review.') + f'\n\n[Open in Gmail]({url})'
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(respond)) as http:
            await TelegramClient(http, trace_base_url='http://localhost:3000/traces').send(
                1, PreparedReply(text, allowed, 'a' * 32))
        if not valid:
            assert sent == []
            return
        assert all(url not in part['text'] for part in sent)
        assert sent[-1]['reply_markup'] == {'inline_keyboard': [[{'text': 'Open in Gmail', 'url': url}]]}
        assert all('reply_markup' not in part for part in sent[:-1])
        assert sent[-1]['text'].endswith('/' + 'a' * 32)

    asyncio.run(scenario())

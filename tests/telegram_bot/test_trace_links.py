import asyncio
import json

import httpx2
import pytest

from core.turns import PreparedReply
from telegram_bot.client import TelegramClient


@pytest.mark.parametrize('enabled,valid', [(True, True), (False, True), (True, False)])
def test_debug_trace_footer_does_not_bypass_delivery_guard(enabled: bool, valid: bool) -> None:
    async def scenario() -> None:
        sent: list[str] = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            sent.append(json.loads(request.content)['text'])
            return httpx2.Response(200, json={'ok': True, 'result': {}})

        async def allowed() -> bool:
            return valid

        async with httpx2.AsyncClient(base_url='https://telegram.invalid/',
                transport=httpx2.MockTransport(respond)) as http:
            client = TelegramClient(http, trace_base_url='http://localhost:3000/project/test/traces' if enabled else '')
            await client.send(1, PreparedReply('Answer', allowed, 'a' * 32))
        if not valid:
            assert sent == []
        else:
            assert sent[0].startswith('Answer')
            assert ('Trace:' in sent[0]) == enabled
            if enabled:
                assert sent[0].endswith('/' + 'a' * 32)

    asyncio.run(scenario())

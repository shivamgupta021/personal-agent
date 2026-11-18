"""Small Telegram Bot API boundary; never expose token-bearing request errors."""

import asyncio
from typing import Any

import httpx2

from telegram_bot.constants import MESSAGE_INTERVAL_SECONDS, MESSAGE_MAX_UTF16_UNITS, POLL_RETRY_SECONDS, POLL_TIMEOUT_SECONDS


class TelegramError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True, retry_after: float = POLL_RETRY_SECONDS) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class TelegramClient:
    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def request(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self.client.post(method, json=payload)
            if response.status_code in (401, 403, 404, 409):
                raise TelegramError('Telegram configuration failed; check token, owner access, and that no webhook or other poller is active.', retryable=False)
            if response.status_code == 429:
                delay = response.json().get('parameters', {}).get('retry_after', POLL_RETRY_SECONDS)
                raise TelegramError('Telegram rate limit reached.', retry_after=max(POLL_RETRY_SECONDS, float(delay)))
            response.raise_for_status()
            body = response.json()
            if body.get('ok') is not True:
                raise ValueError('Unsuccessful response')
            return body['result']
        except (httpx2.HTTPError, ValueError, KeyError, TypeError):
            raise TelegramError('Telegram request failed; check connectivity and bot configuration.') from None

    async def updates(self, offset: int) -> list[dict[str, Any]]:
        result = await self.request('getUpdates', {
            'offset': offset, 'limit': 1, 'timeout': POLL_TIMEOUT_SECONDS, 'allowed_updates': ['message'],
        })
        if not isinstance(result, list):
            raise TelegramError('Telegram returned invalid updates.')
        return result

    async def send(self, chat_id: int, text: str) -> None:
        while text:
            chunk = text.encode('utf-16-le')[:MESSAGE_MAX_UTF16_UNITS * 2].decode('utf-16-le', errors='ignore')
            if len(chunk) < len(text):
                boundary = max(chunk.rfind('\n'), chunk.rfind(' '))
                if boundary > 0:
                    chunk = chunk[:boundary + 1]
            await self.request('sendMessage', {'chat_id': chat_id, 'text': chunk})
            text = text[len(chunk):]
            if text:
                await asyncio.sleep(MESSAGE_INTERVAL_SECONDS)

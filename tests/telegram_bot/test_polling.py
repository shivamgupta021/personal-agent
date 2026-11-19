import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from research.service import ResearchService
from research.tools.firecrawl import Firecrawl
from telegram_bot.client import TelegramClient, TelegramError
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState


def test_owner_receives_answer_and_other_users_never_reach_model(tmp_path: Path) -> None:
    calls: list[str] = []
    replies: list[dict[str, Any]] = []
    updates = [
        {'update_id': 1, 'message': {'from': {'id': 99}, 'chat': {'id': 99, 'type': 'private'}, 'text': 'secret'}},
        {'update_id': 2, 'message': {'from': {'id': 7}, 'chat': {'id': -1, 'type': 'group'}, 'text': 'group'}},
        {'update_id': 3, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': 'public question'}},
    ]

    def telegram(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if request.url.path.endswith('/getUpdates'):
            return httpx2.Response(200, json={'ok': True, 'result': [u for u in updates if u['update_id'] >= body['offset']][:1]})
        replies.append(body)
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    async def research(question: str) -> str:
        calls.append(question)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(500))) as web:
            model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart('Source: https://example.com/policy')]))
            return await ResearchService.run_with_dependencies(question, {'main': model}, Firecrawl(web))

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://api.telegram.org/botTEST/', transport=httpx2.MockTransport(telegram)) as http:
            service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, lambda turn: (research) (turn.text), identity='test-bot:7')
            with service.state.locked():
                for _ in updates:
                    await service.poll_once()
    asyncio.run(run())
    assert calls == ['public question']
    assert replies == [{'chat_id': 7, 'text': 'Source: https://example.com/policy'}]


class TelegramBoundary:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.fail_send = False

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if request.url.path.endswith('/getUpdates'):
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': 10, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': 'question'},
            }]})  # Intentionally repeat even after the offset advanced.
        if self.fail_send:
            raise httpx2.ReadTimeout('secret-token-in-url', request=request)
        self.replies.append(body['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})


def test_restart_does_not_repeat_interrupted_research(tmp_path: Path) -> None:
    boundary = TelegramBoundary()
    calls: list[str] = []

    async def interrupted(question: str) -> str:
        calls.append(question)
        raise asyncio.CancelledError

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(boundary.respond)) as http:
            service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, lambda turn: (interrupted) (turn.text), identity='test-bot:7')
            with service.state.locked():
                try:
                    await service.poll_once()
                except asyncio.CancelledError:
                    pass
            restarted = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, lambda turn: (interrupted) (turn.text), identity='test-bot:7')
            with restarted.state.locked():
                await restarted.poll_once()
    asyncio.run(run())
    assert calls == ['question']
    assert boundary.replies == []
    assert boundary.requests[-1]['offset'] == 11


def test_long_unicode_answer_preserves_citation_and_text(tmp_path: Path) -> None:
    boundary = TelegramBoundary()
    answer = '😀' * 3000 + '\nSource: https://example.com/policy'

    async def research(question: str) -> str:
        return answer

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(boundary.respond)) as http:
            service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, lambda turn: (research) (turn.text), identity='test-bot:7')
            with service.state.locked():
                await service.poll_once()
    asyncio.run(run())
    assert len(boundary.replies) > 1
    assert ''.join(boundary.replies) == answer
    assert all(len(text.encode('utf-16-le')) // 2 <= 4096 for text in boundary.replies)


def test_research_failure_returns_safe_message_and_is_not_replayed(tmp_path: Path) -> None:
    boundary = TelegramBoundary()
    calls: list[str] = []

    async def research(question: str) -> str:
        calls.append(question)
        raise RuntimeError('private provider payload')

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(boundary.respond)) as http:
            service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, lambda turn: (research) (turn.text), identity='test-bot:7')
            with service.state.locked():
                await service.poll_once()
                await service.poll_once()
    asyncio.run(run())
    assert calls == ['question']
    assert len(boundary.replies) == 1
    assert 'send your question again' in boundary.replies[0]
    assert 'private provider payload' not in boundary.replies[0]


def test_uncertain_send_is_not_retried_or_researched_again(tmp_path: Path) -> None:
    boundary = TelegramBoundary()
    boundary.fail_send = True
    calls: list[str] = []

    async def research(question: str) -> str:
        calls.append(question)
        return 'answer'

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(boundary.respond)) as http:
            service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, lambda turn: (research) (turn.text), identity='test-bot:7')
            with service.state.locked():
                try:
                    await service.poll_once()
                except TelegramError as error:
                    assert 'secret-token' not in str(error)
                else:
                    raise AssertionError('Expected a safe delivery failure')
                boundary.fail_send = False
                await service.poll_once()
    asyncio.run(run())
    assert calls == ['question']
    assert boundary.replies == []


def test_second_poller_cannot_process_updates(tmp_path: Path) -> None:
    with PollingState(tmp_path).locked():
        with pytest.raises(RuntimeError, match='Another Telegram poller'):
            with PollingState(tmp_path).locked():
                raise AssertionError('Two pollers acquired the same lock')


def test_start_command_explains_conversation_behavior_without_model(tmp_path: Path) -> None:
    replies: list[str] = []

    def telegram(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if request.url.path.endswith('/getUpdates'):
            return httpx2.Response(200, json={'ok': True, 'result': [{
                'update_id': 1, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': '/start'},
            }]})
        replies.append(body['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})

    async def research(question: str) -> str:
        raise AssertionError('Local help must not consume model usage')

    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as http:
            service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, lambda turn: (research) (turn.text), identity='test-bot:7')
            with service.state.locked():
                await service.poll_once()
    asyncio.run(run())
    assert 'follow-up' in replies[0]
    assert 'survives restarts' in replies[0]


def test_invalid_bot_credentials_stop_polling_without_leaking_token(tmp_path: Path) -> None:
    async def research(question: str) -> str:
        raise AssertionError('Invalid credentials cannot reach research')

    async def run() -> None:
        async with httpx2.AsyncClient(
            base_url='https://telegram.invalid/botSECRET/',
            transport=httpx2.MockTransport(lambda request: httpx2.Response(401, json={'ok': False, 'description': 'SECRET'})),
        ) as http:
            service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7, lambda turn: (research) (turn.text), identity='test-bot:7')
            with pytest.raises(TelegramError) as caught:
                await asyncio.wait_for(service.run(), timeout=0.1)
            assert 'SECRET' not in str(caught.value)
    asyncio.run(run())

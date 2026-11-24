"""Model-session contention without network access or real credentials."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials

from subscriptions.service import SubscriptionService


def test_shared_model_sessions_queue_and_cancel_without_losing_access(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = SubscriptionService(tmp_path)
        await service.session.credentials.save(OpenAIChatGPTCredentials(
            client_id='test', access_token='test', refresh_token='test', scope='chatgpt.tokens.use.direct'))
        entered = asyncio.Event()
        release = asyncio.Event()
        events: list[str] = []

        async def first() -> None:
            async with service.model():
                events.append('first')
                entered.set()
                await release.wait()

        async def next_request(label: str) -> None:
            async with service.model():
                events.append(label)

        active = asyncio.create_task(first())
        await entered.wait()
        cancelled = asyncio.create_task(next_request('cancelled'))
        queued = asyncio.create_task(next_request('queued'))
        await asyncio.sleep(0)
        assert events == ['first']
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        # Independent clients/commands retain the existing immediate rejection policy.
        with pytest.raises(RuntimeError, match='Another command'):
            async with SubscriptionService(tmp_path).model():
                pass
        release.set()
        await asyncio.gather(active, queued)
        assert events == ['first', 'queued']
        await next_request('after')
        assert events[-1] == 'after'

    asyncio.run(scenario())

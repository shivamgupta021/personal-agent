"""Capability introductions should not require a model subscription."""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from pydantic_ai.models import Model

from core.capabilities import CapabilityHelp
from conversations.store import ConversationStore
from core.turns import OwnerTurn
from core.runtime import SubscriptionApplicationService
from subscriptions.service import SubscriptionService


@pytest.mark.django_db(transaction=True)
def test_capability_intro_without_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def model(self: SubscriptionService, name: str = '') -> AsyncIterator[Model]:
        raise AssertionError('A capability introduction must not open a model connection')
        yield

    async def public(message: str) -> str:
        raise AssertionError('No web research for a greeting')

    monkeypatch.setattr(SubscriptionService, 'model', model)
    app = SubscriptionApplicationService(SubscriptionService(tmp_path), public)
    answer = asyncio.run(app.run(OwnerTurn('owner', 'owner', 'hello', 'Hi, what can you help me with?')))
    assert answer == 'I can help you resolve grievances. Just tell me what you need help with.'

    # The deterministic turn remains available to subsequent conversation turns.
    from_history = asyncio.run(ConversationStore().context('owner'))
    assert answer in str(from_history.messages)
    assert 'Hi, what can you help me with?' in str(from_history.messages)


@pytest.mark.parametrize('message', [
    'Hi, help me get a refund from Hosteller',
    'What can you do? Also remember that I prefer short answers',
    'Explain the sentence "what can you help me with"',
])
def test_help_match_does_not_swallow_other_requests(message: str) -> None:
    assert not CapabilityHelp.matches(message)

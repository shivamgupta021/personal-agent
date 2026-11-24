"""Ordinary conversation may finish in its routing call without another model."""
import asyncio
import json

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ModelMessagesTypeAdapter, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn
from grievances.service import ApplicationService
from conversations.models import ConversationRecord


@pytest.mark.django_db(transaction=True)
def test_acknowledgement_and_followup_use_one_call_each_and_persist_history() -> None:
    calls = 0
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert info.output_tools, 'Ordinary conversation must not invoke a second free-text agent'
        payload = next(part.content for message in reversed(messages) for part in message.parts if isinstance(part, UserPromptPart))
        data = json.loads(str(payload))
        if calls == 2:
            assert 'Python and Ruby' in str(data['conversation_history'])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'action': 'conversation', 'conversation_reply': 'Python and Ruby.'})])
    async def run() -> None:
        app = ApplicationService(FunctionModel(respond))
        assert await app.run(OwnerTurn('owner', 'chat', 'one', 'My languages are Python and Ruby. Acknowledge without searching.')) == 'Python and Ruby.'
        assert await app.run(OwnerTurn('owner', 'chat', 'two', 'Which two languages?')) == 'Python and Ruby.'
    asyncio.run(run())
    assert calls == 2
    assert len(ConversationRecord.objects.get(identity='owner').history) == 4


@pytest.mark.django_db(transaction=True)
def test_large_old_tool_results_do_not_require_an_extra_conversation_call() -> None:
    history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart('My languages are Python and Ruby.')]),
        ModelResponse(parts=[ToolCallPart('old_tool', {}, tool_call_id='old')]),
        ModelRequest(parts=[ToolReturnPart('old_tool', 'x' * 20_000, tool_call_id='old')]),
        ModelResponse(parts=[TextPart('Python and Ruby.')])]
    ConversationRecord.objects.create(identity='owner', history=ModelMessagesTypeAdapter.dump_python(history, mode='json'))
    calls = 0
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert calls == 1
        payload = next(part.content for message in reversed(messages) for part in message.parts if isinstance(part, UserPromptPart))
        data = json.loads(str(payload))
        assert 'Python and Ruby' in str(data['conversation_history'])
        assert 'x' * 100 not in str(data['conversation_history'])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'action': 'conversation', 'conversation_reply': 'Python and Ruby.'})])
    answer = asyncio.run(ApplicationService(FunctionModel(respond)).run(OwnerTurn('owner', 'chat', 'followup', 'Which languages?')))
    assert answer == 'Python and Ruby.'
    saved = ConversationRecord.objects.get(identity='owner')
    assert len(saved.history) == 6
    assert 'x' * 20_000 in str(saved.history)  # Projection is not destructive compaction.

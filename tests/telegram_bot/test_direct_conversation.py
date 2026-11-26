"""The orchestrator delegates; the conversation specialist alone answers and persists."""
import asyncio

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn
from core.service import ApplicationService
from conversations.models import ConversationRecord


@pytest.mark.django_db(transaction=True)
def test_acknowledgement_and_followup_delegate_once_and_persist_history() -> None:
    stages = []
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            assert set(info.output_tools[0].parameters_json_schema['properties']) == {'specialist', 'conversation_question'}
            stages.append('router')
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'specialist':'conversation'})])
        stages.append('conversation')
        assert 'Python and Ruby' in str(messages)
        return ModelResponse(parts=[TextPart('Python and Ruby.')])
    async def run() -> None:
        for delivery, message in [('one','My languages are Python and Ruby. Acknowledge without searching.'),('two','Which two languages?')]:
            app = ApplicationService(FunctionModel(respond))
            assert await app.run(OwnerTurn('owner','owner',delivery,message)) == 'Python and Ruby.'
    asyncio.run(run())
    assert stages == ['router','conversation','router','conversation']
    assert len(ConversationRecord.objects.get(identity='owner').history) == 4

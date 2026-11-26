"""Case assistance announces its plan before reading private evidence."""
import asyncio
from pathlib import Path

import pytest
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart

from core.turns import OwnerTurn, PreparedReply
from grievances.service import ApplicationService
from preferences.models import SavedPreference
from tests.telegram_bot.test_gmail import AssistanceHarness


@pytest.mark.django_db(transaction=True)
def test_new_case_plan_precedes_gmail_search(tmp_path: Path) -> None:
    harness = AssistanceHarness(tmp_path)
    messages: list[str] = []

    async def progress(reply: PreparedReply) -> None:
        assert harness.searches == 0
        messages.append(await reply.render())

    async def scenario() -> None:
        await harness.send('I agree to email processing')
        harness.text = 'Help me get my Kettle refund'
        app = ApplicationService(FunctionModel(harness.model), correspondence=harness.correspondence, progress=progress)
        answer = await app.run(OwnerTurn('test:7', 'test:7', 'new-case', harness.text))
        assert len(messages) == 1
        assert 'Plan for Kettle' in messages[0]
        assert 'after your approval' in messages[0]
        assert harness.searches == 1
        assert 'does not confirm a refund' in answer

    try:
        asyncio.run(scenario())
    finally:
        harness.http.close()


@pytest.mark.django_db(transaction=True)
def test_resumed_request_does_not_reinterpret_already_recorded_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from_test = AssistanceHarness(tmp_path)
    original_model = from_test.model
    phase = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result = original_model(messages, info)
        if info.output_tools and 'action' in info.output_tools[0].parameters_json_schema.get('properties', {}):
            part = result.parts[0]
            if isinstance(part, ToolCallPart):
                args = part.args_as_dict()
                if args.get('action') == 'case_help':
                    args['facts'] = [{'key': 'requested_remedy', 'value': 'my Kettle refund' if phase else 'refund',
                        'quote': 'my Kettle refund'}]
                    return ModelResponse(parts=[ToolCallPart(part.tool_name, args)])
        return result

    async def scenario() -> None:
        nonlocal phase
        monkeypatch.setattr(from_test, 'model', respond)
        first = await from_test.send('Help me get my Kettle refund')
        assert 'model provider' in first
        phase = 1
        answer = await from_test.send('I agree to email processing')
        assert 'does not confirm a refund' in answer
        assert 'explicit correction' not in answer

    try:
        asyncio.run(scenario())
    finally:
        from_test.http.close()


@pytest.mark.django_db(transaction=True)
def test_case_inspection_can_also_answer_preference_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = AssistanceHarness(tmp_path)
    original_model = harness.model

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if harness.text.startswith('Show'):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                {'action': 'show', 'include_preferences': True})])
        return original_model(messages, info)

    async def scenario() -> None:
        await harness.send('Create case Kettle')
        await SavedPreference.objects.acreate(identity='test:7', key='reply_style',
            value='short plain English', source_message_id='explicit-request')
        monkeypatch.setattr(harness, 'model', respond)
        answer = await harness.send('Show my case and saved reply preference')
        assert 'Kettle' in answer
        assert 'short plain English' in answer
        assert harness.searches == 0

    try:
        asyncio.run(scenario())
    finally:
        harness.http.close()

"""Public Telegram draft links use provider message identity and never create on open."""
import asyncio
import json
from pathlib import Path

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo

from gmail.models import GmailDraftExport
from tests.telegram_bot.test_gmail import DraftHarness


class LinkHarness(DraftHarness):
    message_id: str = 'abc123'

    def google(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith('/drafts/draft123'):
            return httpx2.Response(200, json={'id': 'draft123', 'message': {'id': 'def456', 'labelIds': ['DRAFT']}})
        result = super().google(request)
        if request.method == 'POST':
            return httpx2.Response(200, json={'id': 'draft123', 'message': {'id': self.message_id}})
        return result

    def model(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if self.text == 'Open my latest Gmail draft':
            part = messages[-1].parts[0]
            assert isinstance(part, UserPromptPart)
            payload = json.loads(str(part.content))
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'action': 'gmail_open_draft',
                'case_id': payload['selected_case']['id'], 'draft_version': 1})])
        return super().model(messages, info)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('message_id', ['abc123', '', 'invalid&compose=new'])
def test_link_and_fallback_then_open_refreshes_without_creating(tmp_path: Path, message_id: str) -> None:
    harness = LinkHarness(tmp_path)
    harness.message_id = message_id
    async def run() -> None:
        await harness.send('I agree to email processing')
        await harness.send('Help me get my Kettle refund')
        answer = await harness.send('Draft the reply in Gmail')
        assert 'Draft v1' in answer
        assert ('#inbox?compose=abc123' in answer) == bool(message_id == 'abc123')
        assert 'Gmail Drafts in owner@example.com' in answer
        answer = await harness.send('Open my latest Gmail draft')
        assert '#inbox?compose=def456' in answer
        assert len(harness.created) == 1
        row = await GmailDraftExport.objects.aget()
        assert row.message_id == 'def456'
    try:
        asyncio.run(run())
    finally:
        harness.http.close()

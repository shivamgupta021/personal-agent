"""Natural Telegram revisions preserve the exact existing Gmail draft."""
import asyncio
import base64
import json
from email.parser import BytesParser
from pathlib import Path

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo

from gmail.models import CaseMailbox, GmailDraftExport
from grievances.models import ComplaintDraft
from tests.telegram_bot.test_gmail import DraftHarness


class RevisionHarness(DraftHarness):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.original = 'Please refund me. My manually added reference is HUMAN-42.'
        self.draft_reads = 0
        self.changed = False
        self.missing = False
        self.revision_version: int | None = None
        self.corrected = False
        self.recipient_header = 'help@example.com'
        self.revised_subject = 'PRIVATE-SUBJECT'
        self.standalone = False

    def google(self, request: httpx2.Request) -> httpx2.Response:
        if self.standalone and request.url.path.endswith('/messages'):
            return httpx2.Response(200, json={'messages': []})
        if '/drafts/draft123' in request.url.path:
            self.draft_reads += 1
            if self.missing:
                return httpx2.Response(404)
            body = self.original + (' changed during generation' if self.changed and self.draft_reads > 1 else '')
            return httpx2.Response(200, json={'id': 'draft123', 'message': {'id': 'm123', 'threadId': '' if self.standalone else 'def1',
                'labelIds': ['DRAFT'], 'payload': {'mimeType': 'text/plain', 'headers': [
                    {'name': 'To', 'value': self.recipient_header}, {'name': 'Subject', 'value': 'PRIVATE-SUBJECT'},
                    {'name': 'From', 'value': 'owner@example.com'},
                    {'name': 'In-Reply-To', 'value': '<reply@example.com>'}],
                    'body': {'data': base64.urlsafe_b64encode(body.encode()).decode()}}}})
        return super().google(request)

    def model(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        properties = info.output_tools[0].parameters_json_schema.get('properties', {}) if info.output_tools else {}
        if self.text.startswith('Revise'):
            if 'action' in properties:
                part = messages[-1].parts[0]
                assert isinstance(part, UserPromptPart)
                payload = json.loads(str(part.content))
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'action': 'case_help',
                    'case_id': payload['selected_case']['id'], 'gmail_draft': True, 'gmail_revision': True,
                    'draft_version': self.revision_version, 'intent_quote': self.text,
                    'gmail_recipient': 'help@example.com' if self.standalone else '',
                    'facts': [{'key': 'refund_amount', 'value': '1200', 'quote': '1200'}] if self.corrected else [],
                    'correction_quote': 'correct the refund amount to 1200' if self.corrected else ''})])
            if 'body' in properties:
                assert self.original in str(messages)
                if self.corrected:
                    assert '"refund_amount": "1200"' in str(messages)
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                    'subject': self.revised_subject, 'body': self.original + (' Refund amount 1200.' if self.corrected else '') + ' Please respond within a week.',
                    'sources': [{'message_id': 'abc1', 'quote': 'PRIVATE-BODY'}]})])
        return super().model(messages, info)


@pytest.mark.django_db(transaction=True)
def test_revision_reads_manual_edits_and_creates_new_version_without_overwrite(tmp_path: Path) -> None:
    harness = RevisionHarness(tmp_path)
    async def run() -> None:
        await harness.send('I agree to email processing')
        await harness.send('Help me get my Kettle refund')
        await harness.send('Draft the reply in Gmail')
        original_payload = dict(harness.created[0])
        answer = await harness.send('Revise the draft to ask for a response within a week')
        assert 'Draft v2' in answer
        assert len(harness.created) == 2 and harness.created[0] == original_payload
        revised = BytesParser().parsebytes(base64.urlsafe_b64decode(harness.created[1]['message']['raw']))
        payload = revised.get_payload(decode=True)
        assert isinstance(payload, bytes)
        assert 'HUMAN-42' in payload.decode()
        assert revised['To'] == 'help@example.com'
        assert harness.created[1]['message']['threadId'] == 'def1'
        assert await ComplaintDraft.objects.acount() == 2
        assert await GmailDraftExport.objects.acount() == 2
        answer = await harness.send('Revise the draft to be shorter')
        assert 'Which version' in answer and len(harness.created) == 2
        harness.revision_version = 1
        harness.corrected = True
        answer = await harness.send('Revise v1 and correct the refund amount to 1200')
        assert 'Draft v3' in answer and len(harness.created) == 3
    try:
        asyncio.run(run())
    finally:
        harness.http.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', ['missing', 'changed'])
def test_revision_stops_if_original_is_missing_or_changed(tmp_path: Path, failure: str) -> None:
    harness = RevisionHarness(tmp_path)
    async def run() -> None:
        await harness.send('I agree to email processing')
        await harness.send('Help me get my Kettle refund')
        await harness.send('Draft the reply in Gmail')
        harness.missing = failure == 'missing'
        harness.changed = failure == 'changed'
        answer = await harness.send('Revise the draft to ask for a response within a week')
        assert 'Draft v2' not in answer
        assert len(harness.created) == 1
        assert await ComplaintDraft.objects.acount() == 1
    try:
        asyncio.run(run())
    finally:
        harness.http.close()


class DraftEvidenceHarness(DraftHarness):
    def google(self, request: httpx2.Request) -> httpx2.Response:
        response = super().google(request)
        if '/threads/' in request.url.path and response.status_code == 200:
            data = response.json()
            data['messages'].extend({'id': f'unsent{index}', 'threadId': 'def1', 'labelIds': ['DRAFT'],
                'payload': {'mimeType': 'text/plain', 'headers': [{'name': 'From', 'value': 'support@example.com'}],
                    'body': {'data': base64.urlsafe_b64encode(b'UNSENT-FALSE-APPROVAL').decode()}}}
                for index in range(25))
            return httpx2.Response(200, json=data)
        return response


@pytest.mark.django_db(transaction=True)
def test_unsent_drafts_never_enter_case_evidence_or_displace_received_mail(tmp_path: Path) -> None:
    harness = DraftEvidenceHarness(tmp_path)
    async def run() -> None:
        await harness.send('I agree to email processing')
        await harness.send('Help me get my Kettle refund')
        row = await CaseMailbox.objects.aget()
        assert [item['message_id'] for item in row.messages] == ['abc1']
        assert not row.truncated
        answer = await harness.send('Draft a reply in Gmail')
        assert 'Draft v1' in answer
        row = await CaseMailbox.objects.aget()
        assert 'UNSENT-FALSE-APPROVAL' not in str(row.messages)
    try:
        asyncio.run(run())
    finally:
        harness.http.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('change_subject', [False, True])
def test_revision_accepts_same_display_address_but_rejects_thread_subject_change(tmp_path: Path, change_subject: bool) -> None:
    harness = RevisionHarness(tmp_path)
    async def run() -> None:
        await harness.send('I agree to email processing')
        await harness.send('Help me get my Kettle refund')
        await harness.send('Draft the reply in Gmail')
        harness.recipient_header = 'Support Team <help@example.com>'
        if change_subject:
            harness.revised_subject = 'New conversation subject'
        answer = await harness.send('Revise the draft subject' if change_subject else 'Revise the draft to be shorter')
        if change_subject:
            assert 'Changing the subject' in answer
            assert len(harness.created) == 1
        else:
            assert 'Draft v2' in answer
            assert len(harness.created) == 2
            saved = await GmailDraftExport.objects.aget(draft__version=2)
            assert saved.recipient == 'help@example.com'
    try:
        asyncio.run(run())
    finally:
        harness.http.close()


@pytest.mark.django_db(transaction=True)
def test_standalone_revision_applies_requested_subject_change(tmp_path: Path) -> None:
    harness = RevisionHarness(tmp_path)
    async def run() -> None:
        await harness.send('I agree to email processing')
        await harness.send('Help me get my Kettle refund')
        await harness.send('Draft the reply in Gmail')
        # Set up an existing standalone draft, then exercise the normal Telegram revision.
        await CaseMailbox.objects.aupdate(selected_thread='', messages=[], candidates=[])
        await GmailDraftExport.objects.aupdate(thread_id='')
        harness.standalone = True
        harness.revised_subject = 'Corrected standalone subject'
        answer = await harness.send('Revise the draft to help@example.com with a corrected subject')
        assert 'Draft v2' in answer
        revised = BytesParser().parsebytes(base64.urlsafe_b64decode(harness.created[1]['message']['raw']))
        assert revised['Subject'] == 'Corrected standalone subject'
        assert 'threadId' not in harness.created[1]['message']
    try:
        asyncio.run(run())
    finally:
        harness.http.close()

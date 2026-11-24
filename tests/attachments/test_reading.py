"""Actual local extraction plus authenticated Telegram/Gmail ingestion boundaries."""
import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import shutil
from threading import Event

import httpx2
from PIL import Image, ImageDraw, ImageFont
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, UserPromptPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from attachments.service import AttachmentExtractor
from attachments.records import Upload, AttachmentText
from core.turns import OwnerTurn
from conversations.store import ConversationStore
from grievances.models import CaseMessage, GrievanceCase
from attachments.constants import MAX_FILE_BYTES
from conversations.models import ConversationRecord
from gmail.mailbox import Mailbox
from grievances.service import ApplicationService
from telegram_bot.client import TelegramClient
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState
from tests.telegram_bot.test_gmail import MailHarness, DraftHarness


def receipt_image(format: str = 'PNG') -> bytes:
    image = Image.new('RGB', (1000, 400), 'white')
    ImageDraw.Draw(image).text((45, 70), 'Receipt AB123\nTotal INR 1250\nRefund pending', fill='black', font=ImageFont.load_default(size=42))
    output = BytesIO()
    image.save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize('mime,format', [('image/png', 'PNG'), ('application/pdf', 'PDF')])
def test_actual_local_ocr_reads_receipt_with_source(mime: str, format: str) -> None:
    if not shutil.which('tesseract') or not shutil.which('pdftoppm'):
        pytest.skip('Install Poppler and Tesseract for local OCR checks')
    result = AttachmentExtractor().extract(receipt_image(format), 'receipt.' + format.lower(), mime, 'fixture')
    assert result.status == 'read'
    assert '1250' in result.text and 'AB123' in result.text
    assert ('page 1' if format == 'PDF' else 'image OCR') in result.text


@pytest.mark.parametrize('data,mime,status', [(b'fake', 'application/zip', 'unsupported'),
    (b'x' * (MAX_FILE_BYTES + 1), 'text/plain', 'too_large'), (b'%PDF-broken', 'application/pdf', 'unreadable')])
def test_extraction_failures_are_explicit(data: bytes, mime: str, status: str) -> None:
    result = AttachmentExtractor().extract(data, 'receipt', mime, 'fixture')
    assert result.status == status and result.notice and not result.text


@pytest.mark.django_db(transaction=True)
def test_owner_upload_is_read_once_and_saved_for_followup(tmp_path: Path) -> None:
    requests: list[str] = []
    replies: list[str] = []
    def telegram(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        if '/file/' in request.url.path:
            return httpx2.Response(200, content=b'Receipt AB123. Total INR 1250. Refund pending.')
        if request.url.path.endswith('getUpdates'):
            return httpx2.Response(200, json={'ok': True, 'result': [{'update_id': 1, 'message': {
                'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'caption': 'What is the total?',
                'document': {'file_id': 'abc', 'file_name': 'receipt.txt', 'mime_type': 'text/plain', 'file_size': 60}}}]})
        if request.url.path.endswith('getFile'):
            return httpx2.Response(200, json={'ok': True, 'result': {'file_path': 'documents/file_1.txt', 'file_size': 60}})
        replies.append(json.loads(request.content)['text'])
        return httpx2.Response(200, json={'ok': True, 'result': {}})
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert not info.function_tools
        payload = next(p.content for m in reversed(messages) for p in m.parts if isinstance(p, UserPromptPart))
        assert '1250' in str(payload) and 'receipt.txt' in str(payload)
        return ModelResponse(parts=[TextPart('INR 1250 (receipt.txt).')])
    async def run() -> None:
        async with httpx2.AsyncClient(base_url='https://api.telegram.org/botTEST/', transport=httpx2.MockTransport(telegram)) as http:
            service = TelegramService(TelegramClient(http), PollingState(tmp_path), 7,
                ApplicationService(FunctionModel(model)).prepare, identity='owner')
            await service.poll_once()
            await service.poll_once()
    asyncio.run(run())
    assert replies == ['INR 1250 (receipt.txt).']
    assert requests.count('/botTEST/getFile') == 1
    assert '1250' in str(ConversationRecord.objects.get(identity='owner').history)


class AttachmentHarness(MailHarness):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.downloads = 0

    def google(self, request: httpx2.Request) -> httpx2.Response:
        if '/attachments/' in request.url.path:
            self.downloads += 1
            return httpx2.Response(200, json={'data': base64.urlsafe_b64encode(b'Refund decision: INR 1250 approved.').decode()})
        if '/threads/' in request.url.path:
            return httpx2.Response(200, json={'messages': [{'id': 'abc1', 'threadId': 'def1', 'payload': {
                'parts': [{'partId': '1', 'mimeType': 'text/plain', 'filename': 'decision.txt',
                    'body': {'attachmentId': 'attachment1', 'size': 35}}]}}]})
        return super().google(request)


def test_gmail_attachment_is_evidence_and_unchanged_parts_are_cached(tmp_path: Path) -> None:
    harness = AttachmentHarness(tmp_path)
    try:
        mailbox = Mailbox(harness.connection, harness.http)
        result = mailbox.thread('def1', 'owner@example.com')
        assert '1250 approved' in result.items[0].text
        assert not result.items[0].unread_attachments
        assert 'decision.txt' in result.items[0].text
        again = mailbox.thread('def1', 'owner@example.com', result.items)
        assert again.items == result.items
        assert harness.downloads == 1
    finally:
        harness.http.close()


@pytest.mark.django_db(transaction=True)
def test_deletion_during_attachment_answer_cannot_restore_the_file() -> None:
    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await ConversationStore().forget('owner')
        return ModelResponse(parts=[TextPart('Sensitive receipt total.')])
    answer = asyncio.run(ApplicationService(FunctionModel(model)).run(OwnerTurn('owner', 'owner', 'upload',
        'Read this', (Upload('receipt.txt', 'text/plain', b'Sensitive receipt total 1250.', 'Telegram'),))))
    assert 'changed' in answer
    assert not ConversationRecord.objects.filter(identity='owner').exists()


@pytest.mark.django_db(transaction=True)
def test_cancellation_drains_extraction_before_turn_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    started, release, finished = Event(), Event(), Event()
    def extract(self: AttachmentExtractor, data: bytes, name: str, mime: str, source: str) -> AttachmentText:
        started.set()
        release.wait(5)
        finished.set()
        return AttachmentText(name, source, 'receipt', 'read')
    monkeypatch.setattr(AttachmentExtractor, 'extract', extract)
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError('Cancelled upload must not reach the model')
    async def run() -> None:
        task = asyncio.create_task(ApplicationService(FunctionModel(model)).run(OwnerTurn('owner', 'owner', 'upload',
            'Read this', (Upload('receipt.txt', 'text/plain', b'receipt', 'Telegram'),))))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
    asyncio.run(run())


class UploadedDraftHarness(DraftHarness):
    def model(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not info.output_tools:
            return ModelResponse(parts=[TextPart('Receipt records INR 1250 (receipt.txt).')])
        if 'body' in info.output_tools[0].parameters_json_schema.get('properties', {}):
            payload = next(p.content for m in reversed(messages) for p in m.parts if isinstance(p, UserPromptPart))
            sources = json.loads(str(payload))['uploaded_evidence']
            assert len(sources) == 1 and 'INR 1250' in next(iter(sources.values()))
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'subject': 'PRIVATE-SUBJECT',
                'body': 'Please review the uploaded receipt for INR 1250.',
                'sources': [{'message_id': next(iter(sources)), 'quote': 'INR 1250'}]})])
        return super().model(messages, info)


@pytest.mark.django_db(transaction=True)
def test_uploaded_case_receipt_is_available_to_gmail_draft_and_deleted_with_case(tmp_path: Path) -> None:
    h = UploadedDraftHarness(tmp_path)
    async def run() -> None:
        await h.send('I agree to email processing')
        await h.send('Help me get my Kettle refund')
        app = ApplicationService(FunctionModel(h.model), correspondence=h.correspondence)
        answer = await app.run(OwnerTurn('test:7', 'test:7', 'upload-receipt', 'Read this receipt',
            (Upload('receipt.txt', 'text/plain', b'Receipt: INR 1250', 'Telegram'),)))
        assert '1250' in answer
        assert await CaseMessage.objects.filter(role='attachment').acount() == 1
        assert 'Draft v1' in await h.send('Draft the reply in Gmail')
        assert not await ConversationRecord.objects.filter(history__icontains='Receipt: INR 1250').aexists()
        await GrievanceCase.objects.all().adelete()
        assert not await CaseMessage.objects.filter(role='attachment').aexists()
    try:
        asyncio.run(run())
    finally:
        h.http.close()


def test_attachment_network_failure_preserves_readable_email(tmp_path: Path) -> None:
    class BrokenAttachment(AttachmentHarness):
        def google(self, request: httpx2.Request) -> httpx2.Response:
            if '/attachments/' in request.url.path:
                raise httpx2.ReadTimeout('private provider error')
            return super().google(request)
    harness = BrokenAttachment(tmp_path)
    try:
        result = Mailbox(harness.connection, harness.http).thread('def1', 'owner@example.com')
        assert result.items[0].unread_attachments
        assert 'Attachment download failed' in result.items[0].text
        assert 'private provider error' not in result.items[0].text
    finally:
        harness.http.close()

"""Explicit reply checking with real storage and controlled mailbox/model boundaries."""
import asyncio
from contextlib import nullcontext
from dataclasses import asdict
import json
from collections.abc import Callable

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn
from gmail.errors import GmailError
from gmail.correspondence import Correspondence
from gmail.mailbox import Mailbox
from gmail.models import CaseMailbox, EmailConsent, GmailDraftExport, GmailReplyCheckState
from gmail.records import ImportedMessage, MailResult
from gmail.reply_checks import GmailReplyChecks
from grievances.models import CaseOperationReceipt, CaseSelection, ComplaintDraft, GrievanceCase, SentReport
from grievances.store import CaseView, SelectionView
from grievances.service import ApplicationService


class ReplyMailbox(Mailbox):
    def __init__(self, result: MailResult[ImportedMessage]) -> None:
        self.result = result
        self.failure = False
        self.on_read: Callable[[], None] = lambda: None
        self.reads = 0
        self.account_valid = True

    def verify_account(self, mailbox: str) -> None:
        if not self.account_valid:
            raise GmailError('Account disconnected or changed')

    def thread(self, thread_id: str, expected_mailbox: str, previous: list[ImportedMessage] | None = None) -> MailResult[ImportedMessage]:
        assert thread_id == 'thread1'
        assert expected_mailbox == 'owner@example.com'
        self.reads += 1
        self.on_read()
        if self.failure:
            raise GmailError('unavailable')
        return self.result


class ReplyHarness:
    def __init__(self) -> None:
        self.case = GrievanceCase.objects.create(owner_id='owner', label='Refund', label_key='refund')
        CaseSelection.objects.create(owner_id='owner', conversation_id='chat', case=self.case)
        EmailConsent.objects.create(owner_id='owner')
        self.old = ImportedMessage('old', 'thread1', 'support@example.com', 'Refund', '', 'Received.', False)
        self.new = ImportedMessage('new', 'thread1', 'support@example.com', 'Refund', '', 'Refund approved.', True)
        self.row = CaseMailbox.objects.create(case=self.case, mailbox='owner@example.com',
            selected_thread='thread1', messages=[asdict(self.old)])
        self.mailbox = ReplyMailbox(MailResult('owner@example.com', [self.old, self.new]))
        self.classification = 'substantive'
        self.bad_quote = False
        self.service = GmailReplyChecks(FunctionModel(self.respond), lambda: nullcontext(self.mailbox))

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        payload = next(part.content for message in reversed(messages) for part in message.parts
            if isinstance(part, UserPromptPart))
        assert isinstance(payload, str)
        inputs = json.loads(payload)['received_messages']
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'findings': [
            {'message_id': item['message_id'], 'classification': self.classification,
             'summary': item['text'], 'quote': 'invented evidence' if self.bad_quote else item['text']}
            for item in inputs]})])

    def check(self) -> str:
        return asyncio.run(self.service.run(OwnerTurn('owner', 'chat', 'check', 'Did they reply?'),
            SelectionView(str(self.case.pk), 0), CaseView(str(self.case.pk), 'Refund', 'open', 0, {}, [], [])))


@pytest.mark.django_db(transaction=True)
def test_reply_check_persists_provenance_deduplicates_and_survives_new_service() -> None:
    harness = ReplyHarness()
    harness.mailbox.result.items.append(harness.new)
    answer = harness.check()
    assert '1 new received' in answer and 'Refund approved.' in answer
    assert 'Attachments were not read' in answer
    state = GmailReplyCheckState.objects.get()
    assert state.seen_message_ids == ['old', 'new']
    assert len(state.findings) == 2
    harness.service = GmailReplyChecks(FunctionModel(harness.respond), lambda: nullcontext(harness.mailbox))
    assert 'No newly received' in harness.check()
    harness.case.refresh_from_db()
    assert harness.case.status == 'open'


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('classification,expected', [
    ('acknowledgement', 'acknowledge receipt'), ('uncertain', 'Do you consider it a substantive reply?')])
def test_acknowledgement_and_uncertainty_are_not_resolution(classification: str, expected: str) -> None:
    harness = ReplyHarness()
    harness.classification = classification
    harness.mailbox.result = MailResult('owner@example.com', [harness.new], truncated=True)
    answer = harness.check()
    assert expected in answer and 'Only part of the thread' in answer
    assert 'not verified' in answer
    harness.case.refresh_from_db()
    assert harness.case.status == 'open'


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', ['provider', 'account', 'quote', 'empty'])
def test_failed_check_preserves_previous_state(failure: str) -> None:
    harness = ReplyHarness()
    harness.check()
    old = GmailReplyCheckState.objects.get().last_summary
    harness.mailbox.failure = failure == 'provider'
    harness.bad_quote = failure == 'quote'
    if failure == 'quote':
        harness.mailbox.result.items.append(ImportedMessage('third', 'thread1', 'support@example.com', 'Refund', '', 'New evidence', False))
    if failure == 'account':
        harness.mailbox.result = MailResult('someone@example.com', [harness.new])
    if failure == 'empty':
        harness.mailbox.result = MailResult('owner@example.com', [])
    assert 'could not verify' in harness.check()
    assert GmailReplyCheckState.objects.get().last_summary == old


@pytest.mark.django_db(transaction=True)
def test_deletion_during_read_never_restores_case_or_reply_state() -> None:
    harness = ReplyHarness()
    harness.mailbox.on_read = lambda: GrievanceCase.objects.filter(pk=harness.case.pk).delete() and None
    assert 'could not verify' in harness.check()
    assert not GmailReplyCheckState.objects.exists()
    assert not CaseMailbox.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_draft_messages_are_not_company_replies_or_sent_evidence() -> None:
    harness = ReplyHarness()
    draft = ImportedMessage('draft', 'thread1', 'owner@example.com', 'Refund', '', 'My draft', False, ('DRAFT',))
    harness.mailbox.result = MailResult('owner@example.com', [draft])
    answer = harness.check()
    assert 'No received reply' in answer and 'not verified' in answer
    assert GmailReplyCheckState.objects.get().sent_matches == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('versions,verified,truncated', [(1, True, False), (2, False, False), (1, False, True)])
def test_sent_evidence_requires_one_matching_export(versions: int, verified: bool, truncated: bool) -> None:
    harness = ReplyHarness()
    for version in range(1, versions + 1):
        operation = CaseOperationReceipt.objects.create(owner_id='owner', conversation_id='chat',
            delivery_id=f'draft-{version}', action='draft', case_id=harness.case.pk, outcome='drafted')
        draft = ComplaintDraft.objects.create(case=harness.case, version=version, fact_revision=0,
            operation=operation, subject='Refund', body='Please refund my purchase.')
        GmailDraftExport.objects.create(draft=draft, mailbox='owner@example.com', recipient='Support <support@example.com>',
            thread_id='thread1', fingerprint=f'fingerprint-{version}', status='created', gmail_id=f'draft-{version}')
    sent = ImportedMessage('sent', 'thread1', 'owner@example.com', 'Refund', '', 'Please refund my purchase.',
        False, ('SENT',), recipient='support@example.com')
    harness.mailbox.result = MailResult('owner@example.com', [*harness.mailbox.result.items, sent], truncated)
    answer = harness.check()
    state = GmailReplyCheckState.objects.get()
    if verified:
        assert 'Gmail verifies sending' in answer
        assert state.sent_matches == [{'provenance': 'gmail_verified', 'message_id': 'sent', 'draft_version': 1}]
        harness.mailbox.result = MailResult('owner@example.com', [harness.new], truncated=True)
        assert 'Gmail verifies sending' in harness.check()
        assert GmailReplyCheckState.objects.get().sent_matches == state.sent_matches
    elif truncated:
        assert 'not verified' in answer
        assert state.sent_matches == []
    else:
        assert 'matches multiple saved draft versions' in answer
        assert state.sent_matches == []
    assert not SentReport.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_without_consent_or_selected_case_no_mail_is_read() -> None:
    harness = ReplyHarness()
    EmailConsent.objects.all().delete()
    assert 'enable email processing' in harness.check()
    assert harness.mailbox.reads == 0
    EmailConsent.objects.create(owner_id='owner')
    CaseSelection.objects.filter(owner_id='owner').update(case=None, revision=1)
    assert 'could not verify' in harness.check()
    assert harness.mailbox.reads == 0


@pytest.mark.django_db(transaction=True)
def test_natural_reply_request_routes_through_public_application_turn() -> None:
    harness = ReplyHarness()
    harness.mailbox.result.items.append(ImportedMessage('unsent', 'thread1', 'owner@example.com',
        'PRIVATE DRAFT', '', 'Unsent instructions must not be imported.', False, ('DRAFT',)))
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        payload = next(part.content for message in reversed(messages) for part in message.parts
            if isinstance(part, UserPromptPart))
        assert isinstance(payload, str)
        if 'received_messages' in json.loads(payload):
            return harness.respond(messages, info)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
            {'action': 'gmail_check_replies', 'case_id': str(harness.case.pk)})])
    app = ApplicationService(FunctionModel(respond),
        correspondence=Correspondence(lambda: nullcontext(harness.mailbox)))
    answer = asyncio.run(app.run(OwnerTurn('owner', 'chat', 'public-check', 'Did they reply to my refund request?')))
    assert 'Refund approved.' in answer
    assert GmailReplyCheckState.objects.get().seen_message_ids == ['old', 'new']
    assert [message['message_id'] for message in CaseMailbox.objects.get().messages] == ['old', 'new']


@pytest.mark.django_db(transaction=True)
def test_new_substantive_reply_is_not_hidden_by_later_acknowledgement() -> None:
    harness = ReplyHarness()
    acknowledgement = ImportedMessage('ack', 'thread1', 'support@example.com', 'Refund', '', 'We received your message.', False)
    harness.mailbox.result = MailResult('owner@example.com', [harness.old, harness.new, acknowledgement])
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'findings': [
            {'message_id': message.message_id,
             'classification': 'substantive' if message.message_id == 'new' else 'acknowledgement',
             'summary': message.text, 'quote': message.text}
            for message in harness.mailbox.result.items]})])
    harness.service = GmailReplyChecks(FunctionModel(respond), lambda: nullcontext(harness.mailbox))
    answer = harness.check()
    assert '2 new received' in answer
    assert 'Substantive response: Refund approved.' in answer
    assert 'Acknowledgement: We received your message.' in answer


@pytest.mark.django_db(transaction=True)
def test_account_change_during_classification_does_not_persist_a_check() -> None:
    harness = ReplyHarness()
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        harness.mailbox.account_valid = False
        return harness.respond(messages, info)
    harness.service = GmailReplyChecks(FunctionModel(respond), lambda: nullcontext(harness.mailbox))
    assert 'could not verify' in harness.check()
    assert not GmailReplyCheckState.objects.exists()
    assert [message['message_id'] for message in CaseMailbox.objects.get().messages] == ['old']

@pytest.mark.django_db(transaction=True)
def test_unchanged_explicit_checks_reuse_analysis_but_changed_content_is_reanalyzed() -> None:
    harness = ReplyHarness()
    calls = 0
    def counted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return harness.respond(messages, info)
    harness.service = GmailReplyChecks(FunctionModel(counted), lambda: nullcontext(harness.mailbox))
    harness.check()
    harness.check()
    assert calls == 1
    assert harness.mailbox.reads == 2  # Fresh provider verification remains mandatory.
    harness.mailbox.result.items[1] = ImportedMessage('new', 'thread1', 'support@example.com',
        'Refund', '', 'Refund rejected.', False)
    assert 'Refund rejected' in harness.check()
    assert calls == 2

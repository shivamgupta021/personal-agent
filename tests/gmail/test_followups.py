"""Public conversation and scheduler tests with controlled model and Gmail boundaries."""
import asyncio
import base64
from email.parser import BytesParser
from pathlib import Path

import httpx2
from gmail.constants import COMPOSE_SCOPES
from gmail.mailbox import Mailbox
from gmail.service import GmailService
from tests.telegram_bot.test_gmail import MemoryCredentials, ProfileGoogle
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn, PreparedReply
from gmail.correspondence import Correspondence
from gmail.followup_constants import FollowupStatus
from gmail.models import CaseMonitor, CaseReminder, FollowupApproval, CaseFollowup, CaseMailbox, EmailConsent, FollowupSend, GmailDraftExport
from gmail.monitoring import MonitoringWorker
from gmail.monitoring_store import MonitoringStore
from gmail.records import DraftEnvelope, ImportedMessage, MailResult
from grievances.models import CaseFactRevision, CaseOperationReceipt, CaseSelection, ComplaintDraft, GrievanceCase
from grievances.service import CaseService
from tests.gmail.test_monitoring import MonitoringMailbox


class SendingMailbox(MonitoringMailbox):
    def __init__(self, messages: list[ImportedMessage], envelope: DraftEnvelope) -> None:
        super().__init__(messages)
        self.envelope = envelope
        self.sent: list[DraftEnvelope] = []
        self.uncertain = False

    def read_draft(self, draft_id: str, mailbox: str) -> DraftEnvelope:
        self.verify_account(mailbox)
        return self.envelope

    def send_message(self, draft: DraftEnvelope, draft_id: str = '') -> str:
        self.verify_account(draft.mailbox)
        self.sent.append(draft)
        identifier = 'sent' + str(len(self.sent))
        self.messages.append(ImportedMessage(identifier, draft.thread_id, draft.mailbox,
            draft.subject, '', draft.body, False, ('SENT',), recipient=draft.recipient))
        if self.uncertain:
            raise TimeoutError('Simulated ambiguous transport')
        return identifier


class FollowupHarness:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        self.case = GrievanceCase.objects.create(owner_id='bot:123', label='Demo Store', label_key='demo store', revision=1)
        CaseFactRevision.objects.create(case=self.case, revision=1, facts={}, sources={}, kind='create')
        CaseSelection.objects.create(owner_id='bot:123', conversation_id='chat', case=self.case)
        EmailConsent.objects.create(owner_id='bot:123')
        old = ImportedMessage('old', 'thread1', 'support@example.com', 'Refund', '', 'Received.', False,
            rfc_message_id='<old@example.com>')
        CaseMailbox.objects.create(case=self.case, mailbox='owner@example.com', selected_thread='thread1', messages=[asdict(old)])
        self.envelope = DraftEnvelope('owner@example.com', 'support@example.com', 'Re: Refund',
            'Please refund the cancelled purchase.', 'thread1', '<old@example.com>')
        receipt = CaseOperationReceipt.objects.create(owner_id='bot:123', conversation_id='chat', delivery_id='draft',
            action='draft', outcome='saved', case_id=self.case.pk)
        draft = ComplaintDraft.objects.create(case=self.case, version=1, fact_revision=1, operation=receipt,
            subject=self.envelope.subject, body=self.envelope.body)
        GmailDraftExport.objects.create(draft=draft, mailbox=self.envelope.mailbox, recipient=self.envelope.recipient,
            thread_id='thread1', fingerprint='test', status='created', gmail_id='draft1')
        self.mailbox = SendingMailbox([old], self.envelope)
        self.model = FunctionModel(self.respond)
        self.app = CaseService(self.model, correspondence=Correspondence(lambda: nullcontext(self.mailbox)))
        self.notifications: list[str] = []
        self.sequence = 0
        self.worker = self.new_worker()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Model]:
        yield self.model

    def new_worker(self) -> MonitoringWorker:
        return MonitoringWorker(self.session, lambda: nullcontext(self.mailbox), self.deliver, clock=lambda: self.now)

    async def deliver(self, reply: PreparedReply) -> None:
        self.notifications.append(await reply.render())

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        value = next(part.content for message in reversed(messages) for part in message.parts if isinstance(part, UserPromptPart))
        assert isinstance(value, str)
        data = json.loads(value)
        result: dict[str, object]
        if 'approved_first_message' in data:
            offer = any('offer' in m['text'] for m in data['correspondence'])
            result = {'action': 'pause' if offer else 'send', 'reason': 'Please review this offer.' if offer else 'No decision yet.',
                'body': '' if offer else ('Please provide a written refund decision.' if data['followup_number'] == 1 else 'I am still awaiting a written decision. Please address this outstanding refund request.')}
        elif 'received_messages' in data:
            result = {'findings': [{'message_id': m['message_id'], 'classification': 'substantive' if 'offer' in m['text'] else 'acknowledgement',
                'summary': m['text'], 'quote': m['text']} for m in data['received_messages']]}
        else:
            request = data['current_request']
            actions = {'Prepare automatic sending': 'gmail_followup_propose', 'Approve this sending plan': 'gmail_followup_approve',
                'Stop automatic sending': 'gmail_followup_stop', 'Sending status': 'gmail_followup_status'}
            result = {'action': actions[request], 'intent_quote': request, 'followup_interval_minutes': 1, 'followup_maximum': 2}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])

    def say(self, text: str) -> str:
        self.sequence += 1
        return asyncio.run(self.app.run(OwnerTurn('bot:123', 'chat', str(self.sequence), text)))

    def tick(self) -> None:
        asyncio.run(self.worker.tick('bot:123'))

    def approve(self) -> None:
        assert 'Approve & send' in self.say('Prepare automatic sending')
        assert 'approved' in self.say('Approve this sending plan')
        CaseFollowup.objects.filter(case=self.case).update(next_due=self.now)


@pytest.mark.django_db(transaction=True)
def test_sending_invalidates_review_reminder_for_that_draft() -> None:
    h = FollowupHarness()
    h.approve()
    reminder = CaseReminder.objects.create(monitor=CaseMonitor.objects.get(), kind='draft_review',
        draft_version=1, due_at=h.now + timedelta(minutes=2), sent_provenance='', sent_reference='')
    h.tick()
    reminder.refresh_from_db()
    assert reminder.status == 'cancelled'
    h.now += timedelta(minutes=3)
    h.tick()
    assert not any('Reminder: review draft' in text for text in h.notifications)


@pytest.mark.django_db(transaction=True)
def test_approval_followups_limit_and_restart() -> None:
    h = FollowupHarness()
    h.say('Prepare automatic sending')
    h.tick()
    assert not h.mailbox.sent
    h.say('Approve this sending plan')
    CaseFollowup.objects.filter(case=h.case).update(next_due=h.now)
    h.tick()
    assert len(h.mailbox.sent) == 1
    assert h.mailbox.sent[0].body == h.envelope.body
    h.worker = h.new_worker()
    h.tick()
    assert len(h.mailbox.sent) == 1
    h.now += timedelta(minutes=1)
    h.tick()
    assert len(h.mailbox.sent) == 2
    h.now += timedelta(minutes=1)
    h.tick()
    assert len(h.mailbox.sent) == 3
    assert h.mailbox.sent[1].body != h.mailbox.sent[2].body
    h.now += timedelta(days=2)
    h.tick()
    assert len(h.mailbox.sent) == 3
    assert CaseFollowup.objects.get(case=h.case).status == FollowupStatus.COMPLETE
    assert FollowupSend.objects.filter(status='sent').count() == 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', [TimeoutError, RuntimeError])
def test_followup_model_failure_pauses_and_notifies_without_sending(failure: type[Exception]) -> None:
    h = FollowupHarness()
    h.approve()
    h.tick()

    def unavailable(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise failure('Controlled provider failure')

    h.model = FunctionModel(unavailable)
    h.now += timedelta(minutes=1)
    h.tick()
    assert len(h.mailbox.sent) == 1
    assert CaseFollowup.objects.get(case=h.case).status == FollowupStatus.PAUSED
    assert any('could not safely complete' in text for text in h.notifications)
    h.now += timedelta(minutes=10)
    h.worker = h.new_worker()
    h.tick()
    assert len(h.mailbox.sent) == 1


@pytest.mark.django_db(transaction=True)
def test_offer_pauses_without_accepting_or_closing_case() -> None:
    h = FollowupHarness()
    h.approve()
    h.tick()
    h.mailbox.messages.append(ImportedMessage('offer', 'thread1', 'support@example.com', 'Refund', '', 'We offer a refund. Do you accept?', False))
    h.now += timedelta(minutes=1)
    h.tick()
    assert len(h.mailbox.sent) == 1
    assert CaseFollowup.objects.get(case=h.case).status == FollowupStatus.PAUSED
    h.case.refresh_from_db()
    assert h.case.status == 'open'
    assert any('review this offer' in text for text in h.notifications)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('interruption', ['stop', 'changed_facts', 'truncated', 'unknown'])
def test_sending_stops_when_authority_or_evidence_is_lost(interruption: str) -> None:
    h = FollowupHarness()
    h.approve()
    if interruption == 'stop':
        h.say('Stop automatic sending')
    elif interruption == 'changed_facts':
        GrievanceCase.objects.filter(pk=h.case.pk).update(revision=2)
    elif interruption == 'truncated':
        h.mailbox.truncated = True
    else:
        h.mailbox.uncertain = True
    h.tick()
    h.worker = h.new_worker()
    h.now += timedelta(days=1)
    h.tick()
    assert len(h.mailbox.sent) == (1 if interruption == 'unknown' else 0)
    assert CaseFollowup.objects.get(case=h.case).status != FollowupStatus.ACTIVE
    if interruption == 'changed_facts':
        assert any('Automatic sending paused' in text for text in h.notifications)


@pytest.mark.django_db(transaction=True)
def test_changed_gmail_draft_requires_a_fresh_plan() -> None:
    h = FollowupHarness()
    h.say('Prepare automatic sending')
    h.mailbox.envelope = DraftEnvelope('owner@example.com', 'support@example.com', 'Re: Refund',
        'Changed reviewed text.', 'thread1', '<old@example.com>')
    assert 'changed after' in h.say('Approve this sending plan')
    h.tick()
    assert not h.mailbox.sent


@pytest.mark.django_db(transaction=True)
def test_draft_edited_after_approval_is_not_sent() -> None:
    h = FollowupHarness()
    h.approve()
    h.mailbox.envelope = DraftEnvelope('owner@example.com', 'support@example.com', 'Re: Refund',
        'Changed after approval.', 'thread1', '<old@example.com>')
    h.tick()
    assert not h.mailbox.sent
    assert CaseFollowup.objects.get().status == FollowupStatus.PAUSED


@pytest.mark.parametrize('draft_id', ['', 'draft1'])
def test_gmail_send_uses_exact_envelope_and_official_endpoint(tmp_path: Path, draft_id: str) -> None:
    credentials = MemoryCredentials()
    assert credentials.connection is not None
    credentials.connection.credentials._scopes = list(COMPOSE_SCOPES)
    calls: list[httpx2.Request] = []
    def send(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        request_body = json.loads(request.content)
        payload = request_body['message'] if draft_id else request_body
        if draft_id:
            assert request_body['id'] == draft_id
        message = BytesParser().parsebytes(base64.urlsafe_b64decode(payload['raw']))
        assert request.url.path == '/gmail/v1/users/me/' + ('drafts/send' if draft_id else 'messages/send')
        assert request.method == 'POST'
        assert message['To'] == 'support@example.com'
        assert message['Cc'] is None and message['Bcc'] is None
        assert message['In-Reply-To'] == '<prior@example.com>'
        body = message.get_payload()
        assert isinstance(body, str) and body.strip() == 'Please refund the cancelled purchase.'
        assert payload['threadId'] == 'abc1'
        return httpx2.Response(200, json={'id': 'abc2', 'threadId': 'abc1'})
    with httpx2.Client(transport=httpx2.MockTransport(send)) as http:
        mailbox = Mailbox(GmailService(credentials, ProfileGoogle(), tmp_path), http)
        result = mailbox.send_message(DraftEnvelope('owner@example.com', 'support@example.com', 'Re: Refund',
            'Please refund the cancelled purchase.', 'abc1', '<prior@example.com>', marker='test-1'), draft_id)
    assert result == 'abc2' and len(calls) == 1


@pytest.mark.django_db(transaction=True)
def test_replacing_an_unsent_proposal_does_not_erase_sent_history() -> None:
    h = FollowupHarness()
    h.approve()
    h.tick()
    h.say('Stop automatic sending')
    original = h.mailbox.envelope
    h.mailbox.envelope = DraftEnvelope(original.mailbox, original.recipient, original.subject,
        'A different request for review.', original.thread_id, original.reply_id)
    assert 'Approve & send' in h.say('Prepare automatic sending')
    h.mailbox.envelope = original
    assert 'already been sent' in h.say('Prepare automatic sending')
    assert FollowupSend.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_original_approval_survives_plan_replacement() -> None:
    h = FollowupHarness()
    h.approve()
    approval = FollowupApproval.objects.get()
    original_terms = approval.terms
    h.say('Stop automatic sending')
    h.say('Prepare automatic sending')
    approval.refresh_from_db()
    assert approval.terms == original_terms
    assert approval.generation != CaseFollowup.objects.get().generation


@pytest.mark.django_db(transaction=True)
def test_proposal_does_not_override_explicit_monitoring_stop() -> None:
    h = FollowupHarness()
    h.say('Prepare automatic sending')
    MonitoringStore.stop(CaseMonitor.objects.get(), 'owner stopped')
    assert 'Approve this sending plan' not in h.say('Prepare automatic sending')
    assert not CaseMonitor.objects.get().active


@pytest.mark.django_db(transaction=True)
def test_repeated_approval_reports_active_plan_without_duplicate_send() -> None:
    h = FollowupHarness()
    h.approve()
    h.tick()
    reply = h.say('Approve this sending plan')
    assert 'active' in reply
    assert 'changed' not in reply
    h.tick()
    assert len(h.mailbox.sent) == 1


@pytest.mark.django_db(transaction=True)
def test_negated_closure_cannot_be_authorized_by_a_shortened_model_quote() -> None:
    h = FollowupHarness()
    h.approve()
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
            {'action': 'close_without_resolution', 'intent_quote': 'close it without a refund'})])
    h.app = CaseService(FunctionModel(respond), correspondence=Correspondence(lambda: nullcontext(h.mailbox)))
    answer = h.say('Do not close it without a refund.')
    h.case.refresh_from_db()
    assert h.case.status == 'open' and 'Nothing was changed' in answer
    assert CaseMonitor.objects.get().active


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('message', [
    'Stop automatic sending and close Demo Store as withdrawn, not refunded.',
    'I’m done with this fictional case. Close it without a refund and stop all reminders and follow-ups.',
])
def test_natural_withdrawal_closes_case_and_stops_all_jobs(message: str) -> None:
    h = FollowupHarness()
    h.approve()
    request = message
    original = h.respond

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        data = json.loads(str(next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart))))
        if data.get('current_request') == request:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                {'action': 'close_without_resolution', 'intent_quote': request})])
        return original(messages, info)

    h.app = CaseService(FunctionModel(respond), correspondence=Correspondence(lambda: nullcontext(h.mailbox)))
    reply = h.say(request)
    h.case.refresh_from_db()
    assert h.case.status == 'closed_without_resolution', reply
    assert not CaseMonitor.objects.get().active
    assert CaseFollowup.objects.get().status == FollowupStatus.STOPPED
    h.tick()
    assert not h.mailbox.sent

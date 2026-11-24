"""Reminder behavior through owner turns and the scheduled worker boundary."""
import asyncio
from dataclasses import asdict
from datetime import timedelta
import json

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn
from gmail.models import CaseMonitor, CaseReminder, MonitorNotification, GmailDraftExport
from gmail.monitoring_store import MonitoringStore
from gmail.records import ImportedMessage
from gmail.reminder_constants import ReminderStatus
from grievances.models import CaseSelection, GrievanceCase, CaseFactRevision, ComplaintDraft, CaseOperationReceipt
from tests.gmail.test_monitoring import MonitoringHarness, ControlledSubscription


class ReminderHarness(MonitoringHarness):
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__()
        self.deadline = self.now + timedelta(minutes=10)
        self.classification = 'acknowledgement'
        self.reminder_kind: str | None = 'no_response'
        self.confirm_substantive = False
        self.truncated = False
        self.sent = ImportedMessage('sent', 'thread1', 'owner@example.com', 'Refund', '', 'Please refund.', False, ('SENT',), 'support@example.com')
        self.row.messages = [asdict(self.sent)]
        self.row.save()
        self.mailbox.messages = [self.sent]
        self.model = FunctionModel(self.respond)
        self.subscription = ControlledSubscription(self.model)
        self.app.model = self.model
        self.app.intake.agent.model = self.model
        self.worker = self.new_worker()
        monkeypatch.setattr('django.utils.timezone.now', lambda: self.now)

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        payload = next(part.content for message in reversed(messages) for part in message.parts if isinstance(part, UserPromptPart))
        assert isinstance(payload, str)
        data = json.loads(payload)
        if 'received_messages' in data:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'findings': [
                {'message_id': item['message_id'], 'classification': self.classification,
                 'summary': item['text'], 'quote': item['text']} for item in data['received_messages']]})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'action': self.action, 'case_id': str(self.case.pk), 'intent_quote': data['current_request'],
            'deadline_quote': data['current_request'], 'reminder_at': self.deadline.isoformat() if self.deadline else None,
            'reply_is_substantive': self.confirm_substantive, 'reminder_kind': self.reminder_kind})])

    def remind(self, action: str = 'gmail_reminder_set') -> str:
        self.action = action
        self.sequence += 1
        return asyncio.run(self.app.run(OwnerTurn('bot:123', 'chat', f'reminder:{self.sequence}',
            'Remind me in ten minutes if they have not replied.' if action == 'gmail_reminder_set' else 'Change my reminder as requested.')))


@pytest.mark.django_db(transaction=True)
def test_deadline_timezone_restart_and_once_only_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    assert '05:40 Asia/Kolkata' in h.remind()
    h.tick()
    assert not h.delivered
    h.worker = h.new_worker()
    h.now = h.deadline + timedelta(minutes=3)
    h.tick()
    assert len(h.delivered) == 1 and 'no substantive reply' in h.delivered[0]
    assert CaseReminder.objects.get().status == ReminderStatus.NOTIFIED
    h.now += timedelta(hours=1)
    h.tick()
    assert len(h.delivered) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('classification,status', [('substantive', ReminderStatus.SATISFIED), ('acknowledgement', ReminderStatus.NOTIFIED), ('uncertain', ReminderStatus.CLARIFY)])
def test_reply_classification_controls_reminder_not_case(monkeypatch: pytest.MonkeyPatch, classification: str, status: str) -> None:
    h = ReminderHarness(monkeypatch)
    h.remind()
    h.classification = classification
    h.mailbox.messages.append(h.new)
    h.now = h.deadline
    h.tick()
    assert CaseReminder.objects.get().status == status
    h.case.refresh_from_db()
    assert h.case.status == 'open'
    if classification != 'acknowledgement':
        assert not any('found no substantive reply' in message for message in h.delivered)


@pytest.mark.django_db(transaction=True)
def test_ambiguous_deadline_and_unsent_draft_do_not_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    h.deadline = None  # type: ignore[assignment]
    assert 'exact date' in h.remind()
    assert not CaseReminder.objects.exists()
    h.deadline = h.now + timedelta(minutes=10)
    h.row.messages = [{**asdict(h.sent), 'labels': ['DRAFT']}]
    h.row.save()
    h.mailbox.messages = []
    assert 'No reminder was scheduled' in h.remind()
    assert not CaseReminder.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_failure_preserves_reminder_and_never_claims_no_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    h.remind()
    h.now = h.deadline
    h.mailbox.failure = True
    h.tick()
    assert CaseReminder.objects.get().status == ReminderStatus.PENDING
    assert all('no substantive reply' not in text for text in h.delivered)
    assert 'unknown' in h.delivered[0]


@pytest.mark.django_db(transaction=True)
def test_reschedule_cancel_and_late_handoff_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    h.remind()
    first = CaseReminder.objects.get()
    note = MonitorNotification.objects.create(monitor=first.monitor, generation=first.monitor.generation,
        reminder_revision=first.revision, text='Old reminder')
    h.deadline += timedelta(days=1)
    h.remind()
    note.refresh_from_db()
    assert note.cancelled
    assert CaseReminder.objects.get().due_at == h.deadline
    h.remind('gmail_reminder_cancel')
    h.now = h.deadline + timedelta(days=2)
    h.tick()
    assert not h.delivered
    assert CaseReminder.objects.get().status == ReminderStatus.CANCELLED


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('event', ['stop', 'disconnect', 'delete'])
def test_lifecycle_cancels_pending_reminder(monkeypatch: pytest.MonkeyPatch, event: str) -> None:
    h = ReminderHarness(monkeypatch)
    h.remind()
    if event == 'stop':
        h.control('gmail_monitor_stop')
    elif event == 'disconnect':
        MonitoringStore.disconnect('123')
    else:
        h.case.delete()
    h.now = h.deadline
    h.tick()
    assert not h.delivered
    assert not CaseReminder.objects.filter(status=ReminderStatus.PENDING).exists()


@pytest.mark.django_db(transaction=True)
def test_uncertain_reply_can_be_explicitly_classified_as_acknowledgement(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    h.remind()
    h.classification = 'uncertain'
    h.mailbox.messages.append(h.new)
    h.now = h.deadline
    h.tick()
    assert CaseReminder.objects.get().status == ReminderStatus.CLARIFY
    h.remind('gmail_reminder_clarify')
    h.tick()
    assert CaseReminder.objects.get().status == ReminderStatus.NOTIFIED
    assert any('no substantive reply' in text for text in h.delivered)


@pytest.mark.django_db(transaction=True)
def test_automatic_case_monitoring_respects_previous_stop() -> None:
    h = MonitoringHarness()
    from_existing = asyncio.run(h.app.store.selection('bot:123', 'chat'))
    current = asyncio.run(h.app.store.get_case('bot:123', str(h.case.pk)))
    assert current is not None
    result = asyncio.run(MonitoringStore().control(OwnerTurn('bot:123', 'chat', 'auto', 'Help me'),
        from_existing, current, 'gmail_monitor_start', h.now, automatic=True))
    assert 'I’ll watch' in result
    h.control('gmail_monitor_stop')
    result = asyncio.run(MonitoringStore().control(OwnerTurn('bot:123', 'chat', 'auto2', 'Help again'),
        from_existing, current, 'gmail_monitor_start', h.now, automatic=True))
    assert result == '' and not CaseMonitor.objects.get().active


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('missing_anchor', [False, True])
def test_incomplete_evidence_asks_instead_of_claiming_no_reply(monkeypatch: pytest.MonkeyPatch, missing_anchor: bool) -> None:
    h = ReminderHarness(monkeypatch)
    h.remind()
    h.mailbox.truncated = True
    if missing_anchor:
        h.mailbox.messages = [h.old]
    h.now = h.deadline
    h.tick()
    assert CaseReminder.objects.get().status == ReminderStatus.CLARIFY
    assert not any('found no substantive reply' in text for text in h.delivered)


@pytest.mark.django_db(transaction=True)
def test_uncertain_notification_delivery_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    h.remind()
    h.now = h.deadline
    h.fail_delivery = True
    h.tick()
    h.fail_delivery = False
    h.worker = h.new_worker()
    h.tick()
    assert not h.delivered
    note = MonitorNotification.objects.get(reminder_revision__gt=0)
    assert note.attempted and not note.delivered


@pytest.mark.django_db(transaction=True)
def test_explicitly_named_case_reminder_does_not_require_separate_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    CaseFactRevision.objects.create(case=h.case, revision=0, kind='create')
    other = GrievanceCase.objects.create(owner_id='bot:123', label='Other', label_key='other')
    CaseSelection.objects.filter(owner_id='bot:123').update(case=other)
    h.action = 'gmail_reminder_set'
    answer = asyncio.run(h.app.run(OwnerTurn('bot:123', 'chat', 'named-case',
        'Remind me in ten minutes about Refund if there is no reply.')))
    assert 'I’ll remind you once' in answer
    assert CaseReminder.objects.get().monitor.mailbox_record.case.pk == h.case.pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('reply_arrived', [False, True])
def test_queued_reminder_rechecks_after_restart(monkeypatch: pytest.MonkeyPatch, reply_arrived: bool) -> None:
    h = ReminderHarness(monkeypatch)
    h.remind()
    h.now = h.deadline

    async def interrupt_handoff(identifier: int, job: object) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr(h.worker.store, 'handoff', interrupt_handoff)
    with pytest.raises(asyncio.CancelledError):
        h.tick()
    assert CaseReminder.objects.get().status == ReminderStatus.QUEUED
    assert not MonitorNotification.objects.get(reminder_revision__gt=0).attempted
    h.worker = h.new_worker()
    if reply_arrived:
        h.classification = 'substantive'
        h.mailbox.messages.append(h.new)
    else:
        h.mailbox.failure = True
    h.tick()
    assert not any('found no substantive reply' in text for text in h.delivered)
    assert CaseReminder.objects.get().status == (ReminderStatus.SATISFIED if reply_arrived else ReminderStatus.QUEUED)
    if not reply_arrived:
        reads = h.mailbox.reads
        h.tick()
        assert h.mailbox.reads == reads  # Failed delivery recheck respects retry backoff.


@pytest.mark.django_db(transaction=True)
def test_later_substantive_reply_clears_pending_clarification(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    h.remind()
    h.classification = 'uncertain'
    h.mailbox.messages.append(h.new)
    h.now = h.deadline
    h.tick()
    assert CaseReminder.objects.get().status == ReminderStatus.CLARIFY
    h.classification = 'substantive'
    h.mailbox.messages.append(ImportedMessage('later', 'thread1', 'support@example.com', 'Refund', '', 'Approved and paid.', False))
    h.now += timedelta(minutes=6)
    h.tick()
    assert CaseReminder.objects.get().status == ReminderStatus.SATISFIED
    h.case.refresh_from_db()
    assert h.case.status == 'open'


@pytest.mark.django_db(transaction=True)
def test_stop_before_correspondence_is_retained() -> None:
    h = MonitoringHarness()
    h.row.delete()
    assert 'Monitoring stopped' in h.control('gmail_monitor_stop')
    h.row.save()
    selection = asyncio.run(h.app.store.selection('bot:123', 'chat'))
    current = asyncio.run(h.app.store.get_case('bot:123', str(h.case.pk)))
    assert current is not None
    answer = asyncio.run(MonitoringStore().control(OwnerTurn('bot:123', 'chat', 'auto-after-stop', 'Help me'),
        selection, current, 'gmail_monitor_start', h.now, automatic=True))
    assert answer == '' and not CaseMonitor.objects.filter(active=True).exists()
    assert 'I’ll watch' in h.control('gmail_monitor_start')


@pytest.mark.django_db(transaction=True)
def test_existing_reply_does_not_satisfy_new_future_reminder(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    h.classification = 'substantive'
    h.mailbox.messages.append(h.old)
    h.remind()
    h.now = h.deadline
    h.tick()
    assert CaseReminder.objects.get().status == ReminderStatus.NOTIFIED
    assert any('since this reminder' in text for text in h.delivered)


@pytest.mark.django_db(transaction=True)
def test_draft_review_reminder_delivers_without_sent_mail_or_model_at_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ReminderHarness(monkeypatch)
    h.reminder_kind = 'draft_review'
    receipt = CaseOperationReceipt.objects.create(owner_id='bot:123', conversation_id='chat', delivery_id='draft', action='draft', outcome='saved')
    CaseFactRevision.objects.create(case=h.case, revision=1, facts={}, kind='create')
    draft = ComplaintDraft.objects.create(case=h.case, version=1, fact_revision=1, operation=receipt, subject='Refund', body='Please review')
    GmailDraftExport.objects.create(draft=draft, mailbox='owner@example.com', recipient='support@example.com', thread_id='thread1', fingerprint='test', status='created', gmail_id='draft1')
    h.row.messages = [asdict(h.old)]
    h.row.save()
    h.mailbox.messages = [h.old]
    h.control()
    h.action = 'gmail_reminder_set'
    answer = asyncio.run(h.app.run(OwnerTurn('bot:123', 'chat', 'review-reminder', 'Remind me in ten minutes to review that draft.')))
    assert 'review draft v1' in answer
    assert h.mailbox.reads == 0
    second_receipt = CaseOperationReceipt.objects.create(owner_id='bot:123', conversation_id='chat', delivery_id='draft2', action='draft', outcome='saved')
    second = ComplaintDraft.objects.create(case=h.case, version=2, fact_revision=1, operation=second_receipt, subject='Refund', body='New draft')
    GmailDraftExport.objects.create(draft=second, mailbox='owner@example.com', recipient='support@example.com', thread_id='thread1', fingerprint='test2', status='created', gmail_id='draft2')
    h.reminder_kind = None
    h.deadline += timedelta(minutes=5)
    answer = asyncio.run(h.app.run(OwnerTurn('bot:123', 'chat', 'move-review', 'Move that reminder five minutes later.')))
    assert 'review draft v1' in answer
    assert CaseReminder.objects.get().draft_version == 1
    assert h.mailbox.reads == 0
    # Hold unrelated periodic monitoring until after this deadline.
    CaseMonitor.objects.update(next_check=h.deadline + timedelta(minutes=5))
    h.now = h.deadline
    h.worker = h.new_worker()
    h.tick()
    assert len(h.delivered) == 1 and 'review draft v1' in h.delivered[0]
    assert h.mailbox.reads == 0
    h.tick()
    assert len(h.delivered) == 1

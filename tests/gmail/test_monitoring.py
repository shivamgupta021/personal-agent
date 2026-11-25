"""Public owner-turn and worker checks against real temporary database storage."""
import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.turns import OwnerTurn, PreparedReply
from gmail.correspondence import Correspondence
from gmail.errors import GmailError
from gmail.mailbox import Mailbox
from gmail.models import CaseMailbox, CaseMonitor, EmailConsent, MonitorNotification
from gmail.monitoring import MonitoringWorker
from gmail.monitoring_store import MonitoringStore
from gmail.records import ImportedMessage, MailResult
from grievances.models import CaseSelection, GrievanceCase
from grievances.service import ApplicationService
from gmail.service import GmailService
from tests.telegram_bot.test_gmail import MemoryCredentials, ProfileGoogle
from subscriptions.auth import SubscriptionBusy


class ControlledSubscription:
    def __init__(self, model: Model) -> None:
        self.controlled_model = model

    @asynccontextmanager
    async def model(self, model_id: str | None = None) -> AsyncIterator[Model]:
        yield self.controlled_model


class MonitoringMailbox(Mailbox):
    def __init__(self, messages: list[ImportedMessage]) -> None:
        self.messages = messages
        self.failure = False
        self.truncated = False
        self.account_valid = True
        self.reads = 0
        self.during_read: Callable[[], None] = lambda: None

    def verify_account(self, mailbox: str) -> None:
        if not self.account_valid:
            raise GmailError('Disconnected')

    def thread(self, thread_id: str, expected_mailbox: str, previous: list[ImportedMessage] | None = None) -> MailResult[ImportedMessage]:
        self.verify_account(expected_mailbox)
        self.reads += 1
        self.during_read()
        if self.failure:
            raise GmailError('Unavailable')
        return MailResult(expected_mailbox, list(self.messages), truncated=self.truncated)


class MonitoringHarness:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        self.case = GrievanceCase.objects.create(owner_id='bot:123', label='Refund', label_key='refund')
        CaseSelection.objects.create(owner_id='bot:123', conversation_id='chat', case=self.case)
        EmailConsent.objects.create(owner_id='bot:123')
        self.old = ImportedMessage('old', 'thread1', 'support@example.com', 'Refund', '', 'Received.', False)
        self.new = ImportedMessage('new', 'thread1', 'support@example.com', 'Refund', '', 'Refund approved.', False)
        self.row = CaseMailbox.objects.create(case=self.case, mailbox='owner@example.com',
            selected_thread='thread1', messages=[asdict(self.old)])
        self.mailbox = MonitoringMailbox([self.old])
        self.action = 'gmail_monitor_start'
        self.delivered: list[str] = []
        self.fail_delivery = False
        self.classifications = 0
        self.model = FunctionModel(self.respond)
        self.subscription = ControlledSubscription(self.model)
        self.app = ApplicationService(self.model, correspondence=Correspondence(lambda: nullcontext(self.mailbox)))
        self.worker = self.new_worker()
        self.sequence = 0

    def new_worker(self) -> MonitoringWorker:
        return MonitoringWorker(self.subscription.model, lambda: nullcontext(self.mailbox), self.send, clock=lambda: self.now)

    async def send(self, reply: PreparedReply) -> None:
        if await reply.valid():
            if self.fail_delivery:
                raise RuntimeError('Uncertain network delivery')
            self.delivered.append(await reply.render())

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        payload = next(part.content for message in reversed(messages) for part in message.parts if isinstance(part, UserPromptPart))
        assert isinstance(payload, str)
        data = json.loads(payload)
        result: dict[str, object]
        if 'received_messages' in data:
            self.classifications += 1
            result = {'findings': [{'message_id': item['message_id'], 'classification': 'substantive',
                'summary': item['text'], 'quote': item['text']} for item in data['received_messages']]}
        else:
            result = {'action': self.action, 'case_id': str(self.case.pk), 'intent_quote': data['current_request']}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])

    def control(self, action: str = 'gmail_monitor_start') -> str:
        self.action = action
        self.sequence += 1
        texts = {'gmail_monitor_start': 'Watch this case for new replies.',
                 'gmail_monitor_stop': 'Stop monitoring this case.', 'gmail_monitor_status': 'Is monitoring on?'}
        return asyncio.run(self.app.run(OwnerTurn('bot:123', 'chat', f'control:{self.sequence}', texts[action])))

    def tick(self) -> None:
        asyncio.run(self.worker.tick('bot:123'))


@pytest.mark.django_db(transaction=True)
def test_consent_lock_defers_monitoring_without_failure_alert(tmp_path: Path) -> None:
    h = MonitoringHarness()
    h.control()
    CaseMonitor.objects.update(next_check=h.now)
    connection = GmailService(MemoryCredentials(), ProfileGoogle(), tmp_path)
    h.mailbox.during_read = connection.require_connection
    with connection.operation():
        h.tick()
    assert not h.delivered
    assert not MonitorNotification.objects.exists()
    assert CaseMonitor.objects.get().failures == 0
    h.now = CaseMonitor.objects.get().next_check
    h.tick()
    assert CaseMonitor.objects.get().failures == 0
    assert h.classifications == 1


@pytest.mark.django_db(transaction=True)
def test_busy_subscription_defers_without_alert() -> None:
    h = MonitoringHarness()
    h.control()
    CaseMonitor.objects.update(next_check=h.now)

    @asynccontextmanager
    async def busy() -> AsyncIterator[Model]:
        raise SubscriptionBusy('Foreground turn holds the lease')
        yield h.model

    h.worker.model_session = busy
    h.tick()
    assert CaseMonitor.objects.get().failures == 0
    assert not MonitorNotification.objects.exists()
    h.worker.model_session = h.subscription.model
    h.now = CaseMonitor.objects.get().next_check
    h.tick()
    assert CaseMonitor.objects.get().failures == 0
    assert h.classifications == 1


@pytest.mark.django_db(transaction=True)
def test_opt_in_restart_catchup_and_duplicate_suppression() -> None:
    h = MonitoringHarness()
    h.tick()
    assert h.mailbox.reads == 0
    assert 'I’ll watch Refund' in h.control()
    # Worker clock is controlled independently of owner-turn clock.
    CaseMonitor.objects.update(next_check=h.now)
    h.mailbox.messages.append(h.new)
    h.tick()
    assert len(h.delivered) == 1 and 'Refund approved.' in h.delivered[0]
    assert MonitorNotification.objects.get().delivered
    h.worker = h.new_worker()
    h.now += timedelta(minutes=20)
    h.tick()
    assert len(h.delivered) == 1
    assert h.classifications == 1
    assert CaseSelection.objects.get(conversation_id='chat').case == h.case
    assert 'Monitoring is on' in h.control('gmail_monitor_status')


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('reason', ['stop', 'closed', 'deleted', 'thread', 'disconnect'])
def test_invalidating_during_read_prevents_notification(reason: str) -> None:
    h = MonitoringHarness()
    h.control()
    CaseMonitor.objects.update(next_check=h.now)
    h.mailbox.messages.append(h.new)
    def invalidate() -> None:
        if reason == 'stop':
            row = CaseMonitor.objects.get()
            MonitoringStore.stop(row, 'owner stopped')
        elif reason == 'closed':
            GrievanceCase.objects.filter(pk=h.case.pk).update(status='resolved')
        elif reason == 'deleted':
            h.case.delete()
        elif reason == 'thread':
            CaseMailbox.objects.filter(pk=h.row.pk).update(selected_thread='other')
        else:
            MonitoringStore.disconnect('123')
    h.mailbox.during_read = invalidate
    h.tick()
    assert not h.delivered
    assert not MonitorNotification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_stop_and_restart_require_new_opt_in() -> None:
    h = MonitoringHarness()
    h.control()
    assert 'stopped' in h.control('gmail_monitor_stop')
    h.worker = h.new_worker()
    h.now += timedelta(days=1)
    h.tick()
    assert h.mailbox.reads == 0
    assert 'off' in h.control('gmail_monitor_status')


@pytest.mark.django_db(transaction=True)
def test_failure_alert_is_suppressed_until_recovery_and_retries_back_off() -> None:
    h = MonitoringHarness()
    h.control()
    CaseMonitor.objects.update(next_check=h.now)
    h.mailbox.failure = True
    h.tick()
    first_due = CaseMonitor.objects.get().next_check
    assert len(h.delivered) == 1 and 'unknown' in h.delivered[0]
    h.now = first_due
    h.tick()
    assert CaseMonitor.objects.get().next_check > first_due + timedelta(minutes=5)
    assert len(h.delivered) == 1
    h.now += timedelta(hours=2)
    h.mailbox.failure = False
    h.tick()
    assert CaseMonitor.objects.get().failures == 0


@pytest.mark.django_db(transaction=True)
def test_uncertain_delivery_is_not_resent_after_restart() -> None:
    h = MonitoringHarness()
    h.control()
    CaseMonitor.objects.update(next_check=h.now)
    h.mailbox.messages.append(h.new)
    h.fail_delivery = True
    h.tick()
    notification = MonitorNotification.objects.get()
    assert notification.attempted and not notification.delivered
    h.fail_delivery = False
    h.worker = h.new_worker()
    h.now += timedelta(minutes=10)
    h.tick()
    assert not h.delivered
    assert 'unconfirmed' in h.control('gmail_monitor_status')


@pytest.mark.django_db(transaction=True)
def test_other_owner_and_unselected_or_draft_only_thread_cannot_enable_monitoring() -> None:
    h = MonitoringHarness()
    h.row.messages = [{**asdict(h.old), 'labels': ['DRAFT']}]
    h.row.save()
    assert 'only have a draft' in h.control()
    assert not CaseMonitor.objects.exists()
    h.row.selected_thread = ''
    h.row.save()
    assert 'find the relevant email' in h.control()
    asyncio.run(h.worker.tick('different-owner'))
    assert not h.mailbox.reads


@pytest.mark.django_db(transaction=True)
def test_pending_notification_survives_worker_restart_before_handoff() -> None:
    h = MonitoringHarness()
    h.control()
    monitor = CaseMonitor.objects.get()
    MonitorNotification.objects.create(monitor=monitor, generation=monitor.generation, text='New reply')
    h.worker = h.new_worker()
    h.tick()
    assert h.delivered == ['New reply']


@pytest.mark.django_db(transaction=True)
def test_replayed_start_does_not_undo_a_later_stop() -> None:
    h = MonitoringHarness()
    h.control()
    h.control('gmail_monitor_stop')
    h.action = 'gmail_monitor_start'
    answer = asyncio.run(h.app.run(OwnerTurn('bot:123', 'chat', 'control:1', 'Watch this case for new replies.')))
    assert 'already processed' in answer
    assert not CaseMonitor.objects.get().active


@pytest.mark.django_db(transaction=True)
def test_one_time_question_does_not_authorize_monitoring_even_if_model_misroutes() -> None:
    h = MonitoringHarness()
    answer = asyncio.run(h.app.run(OwnerTurn('bot:123', 'chat', 'misroute', 'Did they reply?')))
    assert 'explicitly request' in answer
    assert not CaseMonitor.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_pending_notification_is_cancelled_after_stop_even_when_reenabled() -> None:
    h = MonitoringHarness()
    h.control()
    monitor = CaseMonitor.objects.get()
    MonitorNotification.objects.create(monitor=monitor, generation=monitor.generation, text='Old pending reply')
    h.control('gmail_monitor_stop')
    h.control()
    h.tick()
    assert not h.delivered


@pytest.mark.django_db(transaction=True)
def test_two_worker_claims_do_not_poll_same_case_during_lease() -> None:
    h = MonitoringHarness()
    h.control()
    CaseMonitor.objects.update(next_check=h.now)
    first = asyncio.run(MonitoringStore().claim('bot:123', h.now))
    second = asyncio.run(MonitoringStore().claim('bot:123', h.now))
    assert first is not None and second is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('changed_word', [False, True])
def test_monitor_accepts_whitespace_only_quote_changes(changed_word: bool) -> None:
    h = MonitoringHarness()
    h.control()
    h.mailbox.messages = [ImportedMessage('old', 'thread1', 'support@example.com', 'Refund', '',
        'We received your request.\r\n\r\n\r\nReview is pending.', False)]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        quote = 'We received your request.\n\nReview is ' + ('approved.' if changed_word else 'pending.')
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'findings': [{
            'message_id': 'old', 'classification': 'acknowledgement',
            'summary': 'Request received; review pending.', 'quote': quote}]})])

    h.subscription.controlled_model = FunctionModel(respond)
    CaseMonitor.objects.update(next_check=h.now)
    h.tick()
    assert CaseMonitor.objects.get().failures == int(changed_word)
    assert bool(h.delivered) is changed_word


@pytest.mark.django_db(transaction=True)
def test_monitor_retries_quote_assigned_to_wrong_message() -> None:
    h = MonitoringHarness()
    h.control()
    calls = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'findings': [{
            'message_id': 'old', 'classification': 'acknowledgement', 'summary': 'Request received.',
            'quote': 'Refund approved.' if calls == 1 else 'Received.'}]})])

    h.subscription.controlled_model = FunctionModel(respond)
    CaseMonitor.objects.update(next_check=h.now)
    h.tick()
    assert CaseMonitor.objects.get().failures == 0
    assert not h.delivered
    assert calls == 2

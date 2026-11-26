"""Durable opt-in, polling leases and notification handoffs for selected threads."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import F

from core.turns import OwnerTurn
from core.tracing import tracing
from gmail.models import CaseMailbox, CaseMonitor, EmailConsent, MonitorNotification, CaseReminder
from gmail.monitoring_constants import MAX_FAILURE_DELAY_SECONDS, MAX_NOTIFICATION_CHARS, POLL_SECONDS, MAX_BACKOFF_EXPONENT, MAX_DELIVERIES_PER_TICK, SCHEDULER_TICK_SECONDS
from gmail.monitoring_lifecycle import MonitoringLifecycle
from gmail.reply_checks import ReplyCheckResult
from gmail.reminder_store import ReminderStore
from gmail.reminder_constants import ReminderStatus, ReminderKind
from gmail.reply_constants import MAX_SEEN_MESSAGES
from grievances.models import CaseOperationReceipt, CaseSelection, SentReport
from grievances.mutations import CaseMutation
from grievances.ownership import CaseOwnership
from grievances.store import CaseView, SelectionView


@dataclass(frozen=True)
class MonitorJob:
    identifier: int
    generation: int
    owner_id: str
    case_id: str
    label: str
    mailbox: str
    thread_id: str
    epoch: int

    def turn(self) -> OwnerTurn:
        return OwnerTurn(self.owner_id, f'monitor:{self.identifier}',
            'monitor:' + uuid4().hex, 'Check the selected case thread for new replies.')


class MonitoringStore:
    @sync_to_async
    def control(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                action: str, now: datetime, *, automatic: bool = False) -> str:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure or mutation.case is None:
                return 'The case changed. Please try again.'
            if CaseOperationReceipt.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
                    delivery_id=turn.delivery_id).exists():
                return 'This monitoring request was already processed. No additional changes were made.'
            row = CaseMailbox.objects.select_for_update().filter(case=mutation.case).first()
            monitor = CaseMonitor.objects.filter(mailbox_record=row).first() if row else None
            if automatic and (mutation.case.monitoring_paused or monitor is not None or row is None or not row.selected_thread):
                return ''
            if action == 'gmail_monitor_status':
                if monitor is None or not monitor.active:
                    return f'Monitoring is off for {current.label}.'
                uncertain = MonitorNotification.objects.filter(monitor=monitor, attempted=True, delivered=False, cancelled=False).exists()
                return (f'Monitoring is on for {current.label}, approximately every five minutes while the local bot runs.'
                    + (' Reply status is currently unknown; checks are retrying with backoff.' if monitor.failures else '')
                    + (' A previous notification delivery is unconfirmed; it will not be automatically resent.' if uncertain else '')
                    + ' No timely checks occur while your Mac sleeps or the bot is stopped.')
            if action == 'gmail_monitor_stop':
                mutation.case.monitoring_paused = True
                mutation.case.save(update_fields=['monitoring_paused'])
                if monitor:
                    self.stop(monitor, 'owner stopped')
                mutation.record(action.removeprefix('gmail_'), mutation.case)
                return f'Monitoring stopped for {current.label}. It will not resume without your request.'
            if mutation.case.status != 'open':
                return 'Only an open case can be monitored.'
            if not EmailConsent.objects.filter(owner_id=turn.owner_id).exists():
                return 'Please enable email processing before requesting monitoring.'
            if row is None or not row.selected_thread:
                return 'First ask me to find the relevant email for this case so I know which thread to monitor.'
            # A real selected conversation is enough to watch for future correspondence.
            # A draft alone cannot establish that a company was contacted.
            contacted = any('DRAFT' not in message.get('labels', []) and message.get('message_id')
                for message in row.messages) or SentReport.objects.filter(case=mutation.case).exists()
            if not contacted:
                return 'I only have a draft, not established correspondence. Have you contacted the company? Please confirm the sent draft or find the existing email first.'
            if monitor and monitor.active and monitor.mailbox == row.mailbox and monitor.thread_id == row.selected_thread:
                return f'Monitoring is already on for {current.label}.'
            monitor, _ = CaseMonitor.objects.get_or_create(mailbox_record=row,
                defaults={'next_check': now, 'mailbox': row.mailbox, 'thread_id': row.selected_thread})
            mutation.case.monitoring_paused = False
            mutation.case.save(update_fields=['monitoring_paused'])
            monitor.generation += 1
            monitor.active, monitor.mailbox, monitor.thread_id = True, row.mailbox, row.selected_thread
            monitor.seen_message_ids = list(dict.fromkeys(message['message_id'] for message in row.messages))[-MAX_SEEN_MESSAGES:]
            monitor.next_check, monitor.failures, monitor.failure_alerted = now, 0, False
            monitor.stopped_reason = ''
            monitor.save()
            mutation.record(action.removeprefix('gmail_'), mutation.case)
            # A worker's case selection never changes the owner's conversational selection.
            CaseSelection.objects.update_or_create(owner_id=turn.owner_id,
                conversation_id=f'monitor:{monitor.pk}', defaults={'case': mutation.case})
            return (f'I’ll watch {current.label} for new replies about every five minutes while this local bot runs. '
                'I’ll notify you without drafting or sending anything. Your Mac must be awake. Ask me to stop monitoring whenever you want.')

    @staticmethod
    def stop(monitor: CaseMonitor, reason: str) -> None:
        MonitoringLifecycle.stop(CaseMonitor.objects.filter(pk=monitor.pk), reason)
        monitor.refresh_from_db()

    @staticmethod
    def disconnect(owner_suffix: str) -> None:
        """Called before disabling credentials; reconnect never re-enables monitoring."""
        MonitoringLifecycle.stop(CaseMonitor.objects.filter(
            mailbox_record__case__owner_id__endswith=':' + owner_suffix), 'Gmail disconnected')

    @staticmethod
    def eligible(monitor: CaseMonitor) -> bool:
        row = monitor.mailbox_record
        return bool(monitor.active and row.case.status == 'open'
            and monitor.mailbox == row.mailbox and monitor.thread_id == row.selected_thread
            and EmailConsent.objects.filter(owner_id=row.case.owner_id).exists())

    @sync_to_async
    def claim(self, owner_id: str, now: datetime) -> MonitorJob | None:
        with transaction.atomic():
            owner = CaseOwnership.lock(owner_id)
            rows = CaseMonitor.objects.select_for_update().select_related('mailbox_record__case').filter(
                mailbox_record__case__owner_id=owner_id, active=True).order_by('next_check')
            for monitor in rows:
                if not self.eligible(monitor):
                    self.stop(monitor, 'case or correspondence changed')
                    continue
                if monitor.next_check > now:
                    continue
                # Reserve this bounded check across workers/restarts. A crash retries after the lease.
                monitor.next_check = now + timedelta(seconds=POLL_SECONDS)
                monitor.save(update_fields=['next_check'])
                return MonitorJob(monitor.pk, monitor.generation, owner_id,
                    str(monitor.mailbox_record.case.pk), monitor.mailbox_record.case.label,
                    monitor.mailbox, monitor.thread_id, owner.deletion_epoch)
        return None

    def locked_job(self, job: MonitorJob) -> CaseMonitor | None:
        owner = CaseOwnership.lock(job.owner_id)
        if owner.deletion_epoch != job.epoch:
            return None
        monitor = CaseMonitor.objects.select_for_update().select_related('mailbox_record__case').filter(
            pk=job.identifier, generation=job.generation, mailbox_record__case__owner_id=job.owner_id).first()
        if monitor is None or not self.eligible(monitor):
            return None
        return monitor

    @sync_to_async
    def valid(self, job: MonitorJob) -> bool:
        with transaction.atomic():
            return self.locked_job(job) is not None

    @sync_to_async
    def defer(self, job: MonitorJob, now: datetime) -> None:
        with transaction.atomic():
            monitor = self.locked_job(job)
            if monitor is not None:
                monitor.next_check = now + timedelta(seconds=SCHEDULER_TICK_SECONDS)
                monitor.save(update_fields=['next_check'])

    @sync_to_async
    def finish(self, job: MonitorJob, result: ReplyCheckResult, now: datetime) -> None:
        with transaction.atomic():
            monitor = self.locked_job(job)
            if monitor is None:
                return
            text = ''
            if not result.verified:
                monitor.failures += 1
                monitor.next_check = now + timedelta(seconds=min(
                    POLL_SECONDS * 2 ** min(monitor.failures, MAX_BACKOFF_EXPONENT), MAX_FAILURE_DELAY_SECONDS))
                if not monitor.failure_alerted:
                    text = f'{job.label}: I could not check for replies. Status is unknown; I’ll retry with backoff while the bot runs.'
                    monitor.failure_alerted = True
            else:
                fresh = [finding for finding in result.findings if finding.message_id not in monitor.seen_message_ids]
                if fresh:
                    text = f'New correspondence for {job.label}:\n' + '\n'.join(
                        f'- {finding.classification.capitalize()}: {finding.summary}' for finding in fresh)
                    # Preserve retrieval/attachment caveats without repeating the full historical summary.
                    if result.truncated:
                        text += '\nOnly part of the thread was retrieved.'
                    if result.unread_attachments:
                        text += '\nAttachments were not read.'
                    if any(finding.classification == 'uncertain' for finding in fresh):
                        text += '\nI’m unsure whether this answers the issue; please review it.'
                monitor.seen_message_ids = list(dict.fromkeys([*monitor.seen_message_ids,
                    *(finding.message_id for finding in result.findings)]))[-MAX_SEEN_MESSAGES:]
                monitor.failures, monitor.failure_alerted = 0, False
                monitor.next_check = now + timedelta(seconds=POLL_SECONDS)
            monitor.save()
            ReminderStore.assess(monitor, result, now)
            if text:
                # Persist every character before advancing the transaction's seen state.
                # Splitting a notification must not silently discard later findings.
                for offset in range(0, len(text), MAX_NOTIFICATION_CHARS):
                    MonitorNotification.objects.create(monitor=monitor, generation=job.generation, origin_trace_id=tracing.current_trace_id(),
                        text=text[offset:offset + MAX_NOTIFICATION_CHARS])

    @sync_to_async
    def queue_due_reviews(self, owner_id: str, now: datetime) -> None:
        with transaction.atomic():
            CaseOwnership.lock(owner_id)
            reminders = CaseReminder.objects.select_for_update().select_related('monitor__mailbox_record__case').filter(
                monitor__mailbox_record__case__owner_id=owner_id, kind=ReminderKind.DRAFT_REVIEW,
                status=ReminderStatus.PENDING, due_at__lte=now)
            for reminder in reminders:
                monitor = reminder.monitor
                if not self.eligible(monitor):
                    continue
                reminder.status = ReminderStatus.QUEUED
                reminder.save(update_fields=['status'])
                MonitorNotification.objects.create(monitor=monitor, generation=monitor.generation, origin_trace_id=tracing.current_trace_id(),
                    reminder_revision=reminder.revision,
                    text=f'Reminder: review draft v{reminder.draft_version} for {monitor.mailbox_record.case.label} in Gmail. Nothing was sent. You can ask me to open or revise it.')

    @sync_to_async
    def pending(self, owner_id: str) -> list[tuple[int, MonitorJob, str]]:
        notifications = MonitorNotification.objects.select_related('monitor__mailbox_record__case').filter(
            monitor__mailbox_record__case__owner_id=owner_id, monitor__active=True,
            generation=F('monitor__generation'), attempted=False, cancelled=False).order_by('pk')[:MAX_DELIVERIES_PER_TICK]
        with transaction.atomic():
            deletion_epoch = CaseOwnership.lock(owner_id).deletion_epoch
            return [(note.pk, MonitorJob(note.monitor.pk, note.generation, owner_id,
                str(note.monitor.mailbox_record.case.pk), note.monitor.mailbox_record.case.label,
                note.monitor.mailbox, note.monitor.thread_id, deletion_epoch), note.text) for note in notifications]

    @sync_to_async
    def handoff(self, identifier: int, job: MonitorJob) -> bool:
        with transaction.atomic():
            if self.locked_job(job) is None:
                return False
            note = MonitorNotification.objects.filter(pk=identifier, monitor_id=job.identifier).first()
            if note is None or not ReminderStore.notification_valid(note):
                return False
            return bool(MonitorNotification.objects.filter(pk=identifier, attempted=False,
                monitor_id=job.identifier, generation=job.generation).update(attempted=True))

    @sync_to_async
    def delivered(self, identifier: int) -> None:
        with transaction.atomic():
            note = MonitorNotification.objects.filter(pk=identifier, attempted=True).first()
            if note is None or not ReminderStore.notification_valid(note):
                return
            note.delivered = True
            note.save(update_fields=['delivered'])
            if note.reminder_revision:
                CaseReminder.objects.filter(monitor=note.monitor, revision=note.reminder_revision,
                    status=ReminderStatus.QUEUED).update(status=ReminderStatus.NOTIFIED)

    @sync_to_async
    def notification_valid(self, identifier: int) -> bool:
        note = MonitorNotification.objects.filter(pk=identifier).first()
        return note is not None and ReminderStore.notification_valid(note)


    @sync_to_async
    def reminder_delivery_check(self, identifier: int, now: datetime) -> tuple[bool, bool]:
        note = MonitorNotification.objects.select_related('monitor').filter(pk=identifier).first()
        if note is None:
            return False, False
        needs_check = bool(note.reminder_revision and CaseReminder.objects.filter(
            monitor=note.monitor, revision=note.reminder_revision, kind=ReminderKind.NO_RESPONSE, status=ReminderStatus.QUEUED).exists())
        ready = not (needs_check and note.monitor.failures and note.monitor.next_check > now)
        return needs_check, ready

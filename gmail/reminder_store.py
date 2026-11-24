"""One-time case deadlines and evidence-based reminder decisions."""
from datetime import datetime
from email.utils import getaddresses
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async

from core.turns import OwnerTurn
from gmail.models import CaseMonitor, CaseReminder, MonitorNotification
from gmail.reminder_constants import REMINDER_TIMEZONE, ReminderStatus
from gmail.reply_checks import ReplyCheckResult
from grievances.models import SentReport
from grievances.mutations import CaseMutation
from grievances.schema import IntakeAction
from grievances.store import CaseView, SelectionView


class ReminderStore:
    @staticmethod
    def deadline_error(action: IntakeAction, request: str, now: datetime) -> str | None:
        if action.action != 'gmail_reminder_set':
            return None
        if (action.reminder_at is None or not action.deadline_quote or action.deadline_quote not in request
                or action.reminder_at.tzinfo is None):
            return 'What exact date and time should I remind you? I use Asia/Kolkata unless you specify another timezone.'
        if action.reminder_at <= now:
            return 'That deadline is in the past. What future date and time should I use?'
        return None

    @sync_to_async
    def control(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                action: IntakeAction, now: datetime) -> str:
        error = self.deadline_error(action, turn.text, now)
        if error:
            return error
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure or mutation.case is None:
                return 'The case changed. Please try again.'
            monitor = CaseMonitor.objects.select_for_update().select_related('mailbox_record').filter(
                mailbox_record__case=mutation.case).first()
            reminder = CaseReminder.objects.filter(monitor=monitor).first() if monitor else None
            if action.action == 'gmail_reminder_status':
                if reminder is None:
                    return 'There is no saved reminder for this case.'
                return f'{current.label}: reminder {reminder.status}, deadline {self.display(reminder.due_at)}.' + (
                    f' {reminder.reason}' if reminder.reason else '')
            if action.action == 'gmail_reminder_cancel':
                if reminder:
                    MonitorNotification.objects.filter(monitor=monitor, reminder_revision=reminder.revision, attempted=False).update(cancelled=True)
                    reminder.revision += 1
                    reminder.status, reminder.reason = ReminderStatus.CANCELLED, 'Cancelled by you.'
                    reminder.save()
                mutation.record('reminder_cancel', mutation.case)
                return 'The reminder is cancelled. Reply monitoring is unchanged.'
            if monitor is None or not monitor.active or mutation.case.status != 'open':
                return 'The case needs an active selected email thread before I can schedule a no-response reminder.'
            if action.action == 'gmail_reminder_clarify':
                if reminder is None or reminder.status != ReminderStatus.CLARIFY or action.reply_is_substantive is None:
                    return 'Please clarify whether the reply answers the issue; no reminder was changed.'
                MonitorNotification.objects.filter(monitor=monitor, reminder_revision=reminder.revision, attempted=False).update(cancelled=True)
                reminder.revision += 1
                if action.reply_is_substantive:
                    reminder.status, reminder.reason = ReminderStatus.SATISFIED, 'You confirmed a substantive reply.'
                else:
                    reminder.dismissed_message_ids = list(dict.fromkeys([
                        *reminder.dismissed_message_ids, *reminder.question_message_ids]))
                    reminder.status, reminder.reason = ReminderStatus.PENDING, ''
                    monitor.next_check = now
                    monitor.save(update_fields=['next_check'])
                reminder.question_message_ids = []
                reminder.save()
                mutation.record('reminder_reply', mutation.case)
                return 'Reply assessment saved. ' + ('The no-response reminder is cancelled; the case remains open.'
                    if action.reply_is_substantive else 'I’ll recheck before delivering the reminder.')
            row = monitor.mailbox_record
            sent = [message for message in row.messages if 'SENT' in message.get('labels', [])
                and 'DRAFT' not in message.get('labels', [])
                and [address.casefold() for _, address in getaddresses([message.get('sender', '')])] == [row.mailbox.casefold()]]
            report = SentReport.objects.filter(case=mutation.case).order_by('-pk').first()
            if not sent and report is None:
                return 'I have not established that the company was contacted. Send the draft in Gmail, then ask me to check, or explicitly tell me which draft version you sent.'
            due_at = action.reminder_at
            if due_at is None:
                return 'Please specify the reminder deadline.'
            reminder, _ = CaseReminder.objects.get_or_create(monitor=monitor,
                defaults={'due_at': due_at, 'sent_provenance': '', 'sent_reference': ''})
            MonitorNotification.objects.filter(monitor=monitor, reminder_revision=reminder.revision, attempted=False).update(cancelled=True)
            reminder.revision += 1
            reminder.status, reminder.due_at, reminder.reason = ReminderStatus.PENDING, due_at, ''
            reminder.sent_provenance = 'gmail_verified' if sent else 'owner_reported'
            reminder.sent_reference = sent[-1]['message_id'] if sent else str(report.pk) if report else ''
            reminder.baseline_message_ids = [message['message_id'] for message in row.messages]
            reminder.dismissed_message_ids, reminder.question_message_ids = [], []
            reminder.save()
            monitor.next_check = min(monitor.next_check, due_at)
            monitor.save(update_fields=['next_check'])
            mutation.record('reminder_set', mutation.case)
            provenance = 'Gmail confirms sent correspondence.' if sent else 'Sending is based on your report, not verified in Gmail; I’ll watch for new replies from now.'
            return (f'I’ll remind you once on {self.display(due_at)} if no substantive reply is established after checking the thread. '
                f'{provenance} The local bot must be running; after sleep or restart I’ll check before notifying you.')

    @staticmethod
    def display(value: datetime) -> str:
        return value.astimezone(ZoneInfo(REMINDER_TIMEZONE)).strftime('%d %b %Y at %H:%M Asia/Kolkata')

    @staticmethod
    def assess(monitor: CaseMonitor, result: ReplyCheckResult, now: datetime) -> None:
        """Caller holds owner and monitor locks; state and notification commit together."""
        reminder = CaseReminder.objects.select_for_update().filter(monitor=monitor, status=ReminderStatus.PENDING).first()
        if reminder is None or not result.verified:
            return
        after = list(result.message_ids)
        if reminder.sent_provenance == 'gmail_verified':
            if reminder.sent_reference not in after:
                if now >= reminder.due_at:
                    ReminderStore.question(reminder, 'I cannot locate the sent message in the retrieved thread, so I cannot establish whether a reply followed it. Please review the correspondence.', [])
                return
            after = after[after.index(reminder.sent_reference) + 1:]
        else:
            # No provider anchor exists: report only new replies since this reminder was set.
            after = [identifier for identifier in after if identifier not in reminder.baseline_message_ids]
        relevant = [finding for finding in result.findings if finding.message_id in after
            and finding.message_id not in reminder.dismissed_message_ids]
        if any(finding.classification == 'substantive' for finding in relevant):
            reminder.status, reminder.reason = ReminderStatus.SATISFIED, 'Substantive reply received; case outcome unchanged.'
            reminder.save()
            return
        uncertain = [finding.message_id for finding in relevant if finding.classification == 'uncertain']
        if uncertain:
            ReminderStore.question(reminder, 'A reply may answer your case, but I cannot tell reliably. Does it substantively answer the issue? The no-response reminder is waiting for clarification.', uncertain)
            return
        if now < reminder.due_at:
            monitor.next_check = min(monitor.next_check, reminder.due_at)
            monitor.save(update_fields=['next_check'])
            return
        if result.truncated:
            ReminderStore.question(reminder, 'The retrieved thread is incomplete. I cannot safely assert that no reply arrived. Please review the thread before following up.', [])
            return
        reminder.status = ReminderStatus.QUEUED
        reminder.save(update_fields=['status'])
        qualification = ('after your sent message' if reminder.sent_provenance == 'gmail_verified'
            else 'since this reminder was set; sending remains owner-reported')
        MonitorNotification.objects.create(monitor=monitor, generation=monitor.generation,
            reminder_revision=reminder.revision,
            text=f'Reminder for {monitor.mailbox_record.case.label}: I checked the selected thread and found no substantive reply {qualification}. You can review the case or ask me for a follow-up draft. Nothing was sent.')

    @staticmethod
    def question(reminder: CaseReminder, text: str, message_ids: list[str]) -> None:
        reminder.status = ReminderStatus.CLARIFY
        reminder.question_message_ids = message_ids
        reminder.reason = text
        reminder.save()
        MonitorNotification.objects.create(monitor=reminder.monitor, generation=reminder.monitor.generation,
            reminder_revision=reminder.revision, text=text)

    @staticmethod
    def notification_valid(note: MonitorNotification) -> bool:
        return not note.cancelled and (note.reminder_revision == 0 or CaseReminder.objects.filter(monitor=note.monitor,
            revision=note.reminder_revision, status__in=[ReminderStatus.QUEUED, ReminderStatus.CLARIFY]).exists())

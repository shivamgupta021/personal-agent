"""Approval, leases, and durable send receipts for a single case policy."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from asgiref.sync import sync_to_async
from django.db import transaction

from core.turns import OwnerTurn
from gmail.followup_constants import FollowupStatus, LEASE_SECONDS, SendStatus
from gmail.models import CaseFollowup, CaseMonitor, FollowupSend, MonitorNotification, FollowupApproval
from gmail.monitoring_store import MonitoringStore
from gmail.records import DraftEnvelope
from grievances.mutations import CaseMutation
from grievances.models import CaseSelection
from grievances.ownership import CaseOwnership
from grievances.store import CaseView, SelectionView


@dataclass(frozen=True)
class FollowupJob:
    identifier: int
    generation: int
    owner_id: str
    case_id: str
    count: int
    first: DraftEnvelope
    baseline: tuple[str, ...]
    sent: tuple[dict[str, object], ...]

    def turn(self) -> OwnerTurn:
        return OwnerTurn(self.owner_id, 'followup:' + str(self.identifier),
            f'followup:{self.identifier}:{self.generation}:{self.count}', 'Execute approved case follow-up.')


class FollowupStore:
    @staticmethod
    def cadence(minutes: int) -> str:
        for size, unit in ((1440, 'day'), (60, 'hour'), (1, 'minute')):
            if minutes % size == 0:
                count = minutes // size
                return f'{count} {unit}{"s" if count != 1 else ""}'
        return f'{minutes} minutes'

    @staticmethod
    def eligible(policy: CaseFollowup) -> bool:
        monitor = policy.monitor
        row = monitor.mailbox_record
        first = DraftEnvelope(**policy.envelope)
        return (MonitoringStore.eligible(monitor) and policy.fact_revision == row.case.revision
            and first.mailbox == row.mailbox and first.thread_id == row.selected_thread)

    @staticmethod
    def notify(policy: CaseFollowup, text: str) -> None:
        MonitorNotification.objects.create(monitor=policy.monitor, generation=policy.monitor.generation,
            text=f'{policy.case.label}: {text}')

    @sync_to_async
    def propose(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                envelope: DraftEnvelope, version: int, interval: int, maximum: int, now: datetime) -> str:
        with CaseMutation(turn, selection, current, operation='policy') as mutation:
            if mutation.receipt is not None:
                return 'This sending-plan request was already processed; no changes were repeated.'
            if mutation.failure:
                return 'The case changed. Please request the plan again.'
            monitor = CaseMonitor.objects.select_related('mailbox_record__case').filter(mailbox_record__case_id=current.id).first()
            if monitor is None or not MonitoringStore.eligible(monitor):
                return 'Please connect the case email thread before enabling automatic follow-ups.'
            existing = CaseFollowup.objects.filter(case_id=current.id).first()
            if existing and existing.status in (FollowupStatus.ACTIVE, FollowupStatus.UNKNOWN):
                return 'This case already has an active or unconfirmed send plan. Stop it and inspect Sent Mail before replacing it.'
            if FollowupSend.objects.filter(policy__case_id=current.id, sequence=0,
                    envelope__mailbox=envelope.mailbox, envelope__recipient=envelope.recipient,
                    envelope__thread_id=envelope.thread_id, envelope__body=envelope.body).exists():
                return 'That approved message has already been sent. Please review a new draft before starting another sending plan.'
            policy, _ = CaseFollowup.objects.update_or_create(case_id=current.id, defaults={
                'monitor': monitor, 'status': FollowupStatus.PROPOSED,
                'generation': existing.generation + 1 if existing else 1, 'fact_revision': current.revision,
                'envelope': asdict(envelope), 'baseline_ids': [m['message_id'] for m in monitor.mailbox_record.messages],
                'draft_version': version, 'interval_minutes': interval, 'max_followups': maximum,
                'sent_count': 0, 'next_due': now, 'approved_at': None, 'reason': ''})
            mutation.record('followup_propose', mutation.case)
            return (f'For {current.label}, I’ll send draft v{version} to {envelope.recipient}, then follow up '
                f'every {self.cadence(interval)}, at most {maximum} follow-up{"s" if maximum != 1 else ""}. Follow-ups stay in this thread and become '
                'firmer when unanswered. I’ll pause for offers, payment claims, or questions needing your decision. '
                'The local bot must remain running. Approve this sending plan, or tell me what to change.')

    @sync_to_async
    def control(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, action: str, now: datetime, expected_generation: int | None = None) -> str:
        with CaseMutation(turn, selection, current, operation='policy') as mutation:
            if mutation.receipt is not None:
                return 'This sending-plan request was already processed; no changes were repeated.'
            if mutation.failure:
                return 'The case changed. Please try again.'
            policy = CaseFollowup.objects.select_related('case', 'monitor__mailbox_record__case').filter(case_id=current.id).first()
            if policy is None:
                return 'There is no automatic sending plan for this case yet. Ask me to prepare one from its Gmail draft.'
            if action == 'gmail_followup_stop':
                policy.status, policy.reason = FollowupStatus.STOPPED, 'Owner stopped sending.'
                policy.save(update_fields=['status', 'reason'])
                mutation.record('followup_stop', mutation.case)
                return 'Automatic sending stopped. Reply monitoring remains on.'
            if action == 'gmail_followup_status':
                return f'Automatic sending: {policy.status}. Confirmed sends: {policy.sent_count}. {policy.reason}'
            if expected_generation != policy.generation:
                return 'The sending plan changed while approval was being checked. Please review it again.'
            if policy.status != FollowupStatus.PROPOSED:
                return f'The sending plan is {policy.status}; this approval did not schedule another email.'
            if not self.eligible(policy):
                return 'The case or connection changed. Please request a fresh sending plan.'
            policy.status, policy.approved_at, policy.next_due = FollowupStatus.ACTIVE, now, now
            FollowupApproval.objects.create(policy=policy, generation=policy.generation, approved_at=now,
                delivery_id=turn.delivery_id, conversation_id=turn.conversation_id, terms={
                    'envelope': policy.envelope, 'interval_minutes': policy.interval_minutes,
                    'max_followups': policy.max_followups, 'draft_version': policy.draft_version,
                    'fact_revision': policy.fact_revision})
            policy.save(update_fields=['status', 'approved_at', 'next_due'])
            mutation.record('followup_approve', mutation.case)
            return 'Sending plan approved. I’ll send the reviewed message and keep you updated here.'

    @sync_to_async
    def claim(self, owner_id: str, now: datetime) -> FollowupJob | None:
        with transaction.atomic():
            CaseOwnership.lock(owner_id)
            policies = CaseFollowup.objects.select_for_update().select_related('case', 'monitor__mailbox_record__case').filter(
                case__owner_id=owner_id, status=FollowupStatus.ACTIVE, next_due__lte=now).order_by('next_due')
            for policy in policies:
                if not self.eligible(policy):
                    policy.status, policy.reason = FollowupStatus.PAUSED, 'Case, connection, or monitoring changed.'
                    policy.save(update_fields=['status', 'reason'])
                    self.notify(policy, 'Automatic sending paused. ' + policy.reason)
                    continue
                if FollowupSend.objects.filter(policy=policy, generation=policy.generation, sequence=policy.sent_count).exists():
                    policy.status, policy.reason = FollowupStatus.UNKNOWN, 'Previous send outcome is unconfirmed. Check Sent Mail; no automatic retry.'
                    policy.save(update_fields=['status', 'reason'])
                    self.notify(policy, policy.reason)
                    continue
                policy.next_due = now + timedelta(seconds=LEASE_SECONDS)
                policy.save(update_fields=['next_due'])
                CaseSelection.objects.update_or_create(owner_id=owner_id, conversation_id='followup:' + str(policy.pk), defaults={'case': policy.case})
                sent = tuple(FollowupSend.objects.filter(policy=policy, generation=policy.generation,
                    status=SendStatus.SENT).order_by('sequence').values('envelope', 'created_at'))
                return FollowupJob(policy.pk, policy.generation, owner_id, str(policy.case.pk), policy.sent_count,
                    DraftEnvelope(**policy.envelope), tuple(policy.baseline_ids), sent)
        return None

    def locked(self, job: FollowupJob) -> CaseFollowup | None:
        CaseOwnership.lock(job.owner_id)
        policy = CaseFollowup.objects.select_for_update().select_related('case', 'monitor__mailbox_record__case').filter(
            pk=job.identifier, generation=job.generation, case__owner_id=job.owner_id,
            status=FollowupStatus.ACTIVE, sent_count=job.count).first()
        return policy if policy and self.eligible(policy) else None

    @sync_to_async
    def pause(self, job: FollowupJob, reason: str) -> None:
        with transaction.atomic():
            policy = self.locked(job)
            if policy:
                policy.status, policy.reason = FollowupStatus.PAUSED, reason
                policy.save(update_fields=['status', 'reason'])
                self.notify(policy, 'Automatic sending paused. ' + reason)

    @sync_to_async
    def reserve(self, job: FollowupJob, envelope: DraftEnvelope) -> int | None:
        with transaction.atomic():
            policy = self.locked(job)
            if policy is None:
                return None
            attempt, created = FollowupSend.objects.get_or_create(policy=policy, generation=job.generation,
                sequence=job.count, defaults={'envelope': asdict(envelope)})
            return attempt.pk if created else None

    @sync_to_async
    def finish(self, job: FollowupJob, attempt_id: int, message_id: str, now: datetime) -> None:
        with transaction.atomic():
            CaseOwnership.lock(job.owner_id)
            attempt = FollowupSend.objects.select_related('policy__case', 'policy__monitor').filter(pk=attempt_id,
                policy_id=job.identifier, generation=job.generation).first()
            if attempt is None:
                return
            policy = attempt.policy
            attempt.status, attempt.message_id = (SendStatus.SENT if message_id else SendStatus.UNKNOWN), message_id
            attempt.save(update_fields=['status', 'message_id'])
            if policy.generation != job.generation:
                return
            if policy.status not in (FollowupStatus.ACTIVE, FollowupStatus.UNKNOWN):
                if message_id:
                    policy.sent_count += 1
                    policy.save(update_fields=['sent_count'])
                return
            if not message_id:
                policy.status, policy.reason = FollowupStatus.UNKNOWN, 'Send outcome unconfirmed. Check Sent Mail; I will not retry automatically.'
            else:
                policy.sent_count += 1
                policy.next_due = now + timedelta(minutes=policy.interval_minutes)
                if policy.sent_count >= policy.max_followups + 1:
                    policy.status = FollowupStatus.COMPLETE
                policy.reason = ''
            policy.save()
            self.notify(policy, (f'Sent {"the approved message" if job.count == 0 else "follow-up " + str(job.count)} to {job.first.recipient}.'
                + (' The approved send limit is reached; I’ll keep watching for replies.' if policy.status == FollowupStatus.COMPLETE else '')) if message_id else policy.reason)

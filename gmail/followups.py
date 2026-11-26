"""Execute approved correspondence through the existing local scheduler."""
import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import asdict, replace
from datetime import datetime
import json
import logging
from typing import Literal

from asgiref.sync import sync_to_async
from django.db import transaction
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from core.tracing import tracing
from core.turns import OwnerTurn
from core.workers import drain_on_cancel
from gmail import constants
from gmail.followup_constants import MAX_BODY_CHARS, MAX_BODY_WORDS, MAX_DECISION_TOKENS, MAX_JOBS_PER_TICK, MAX_REASON_CHARS
from gmail.followup_prompts import FOLLOWUP_INSTRUCTIONS
from gmail.followup_store import FollowupJob, FollowupStore
from gmail.mailbox import Mailbox
from gmail.models import CaseMailbox, CaseFollowup, GmailDraftExport
from gmail.records import DraftEnvelope, ImportedMessage, MailResult
from gmail.revisions import DraftRevisions
from gmail.monitoring_store import MonitoringStore
from core.lifecycle import ApplicationTurn
from grievances.schema import IntakeAction
from grievances.store import CaseView, SelectionView

logger = logging.getLogger(__name__)


class FollowupDecision(BaseModel):
    action: Literal['send', 'pause']
    body: str = Field(default='', max_length=MAX_BODY_CHARS)
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS)


class FollowupService:
    def __init__(self, mailbox: Callable[[], AbstractContextManager[Mailbox]]) -> None:
        self.mailbox = mailbox
        self.store = FollowupStore()

    async def control(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                      action: IntakeAction, now: datetime, expected_generation: int | None = None) -> str:
        if not action.intent_quote or action.intent_quote not in turn.text:
            return 'Please state the sending change in your own words.'
        approved_generation = None
        if action.action == 'gmail_followup_approve':
            policy = await CaseFollowup.objects.filter(case_id=current.id, status='proposed').afirst()
            if policy is not None and expected_generation is not None and policy.generation != expected_generation:
                return 'That sending plan has changed. Please review the latest plan before approving.'
            if policy is not None:
                approved_generation = policy.generation
                row = await DraftRevisions(self.mailbox).resolve(turn, selection, current,
                    policy.envelope['mailbox'], policy.envelope['thread_id'], policy.draft_version)
                with self.mailbox() as mailbox:
                    reviewed = await sync_to_async(mailbox.read_draft)(row.gmail_id, row.mailbox)
                if asdict(reviewed) != policy.envelope:
                    return 'The Gmail draft changed after I proposed the plan. Please request a fresh plan so you approve the current text.'
        if action.action != 'gmail_followup_propose':
            return await self.store.control(turn, selection, current, action.action, now, approved_generation)
        # Resolve the exact reviewed draft. A proposal confers no sending authority.
        correspondence = await CaseMailbox.objects.filter(case_id=current.id).afirst()
        if correspondence is None or not correspondence.selected_thread:
            return 'Please find the case email thread before planning automatic sends.'
        source = await DraftRevisions(self.mailbox).read(turn, selection, current,
            correspondence.mailbox, correspondence.selected_thread, action.draft_version)
        envelope = source.envelope
        await MonitoringStore().control(turn.followup(), selection, current, 'gmail_monitor_start', now, automatic=True)
        return await self.store.propose(turn, selection, current, envelope, source.version,
            action.followup_interval_minutes, action.followup_maximum, now)



class FollowupWorker:
    def __init__(self, model_session: Callable[[], AbstractAsyncContextManager[Model]],
                 mailbox: Callable[[], AbstractContextManager[Mailbox]], clock: Callable[[], datetime]) -> None:
        self.model_session, self.mailbox, self.clock = model_session, mailbox, clock
        self.store = FollowupStore()

    async def tick(self, owner_id: str) -> None:
        for _ in range(MAX_JOBS_PER_TICK):
            job = await self.store.claim(owner_id, self.clock())
            if job is None:
                return
            async def execute(turn: OwnerTurn, epoch: int) -> str:
                try:
                    return await self.execute(job)
                except TimeoutError:
                    # ApplicationTurn presents timeouts as replies. Persist the worker's
                    # stopping decision before that foreground error conversion.
                    await self.store.pause(job, 'I could not safely complete this follow-up because it timed out. Please review the case.')
                    raise
            try:
                await ApplicationTurn().prepare(job.turn(), execute)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('Automatic follow-up failed before a confirmed outcome; preserving durable send state')
                await self.store.pause(job, 'I could not safely complete this follow-up. Please review the case.')

    async def read(self, job: FollowupJob) -> MailResult[ImportedMessage]:
        with self.mailbox() as mailbox, tracing.operation('gmail.followup_read', {'thread_id': job.first.thread_id}, private=True) as span:
            result = await drain_on_cancel(sync_to_async(mailbox.thread)(job.first.thread_id, job.first.mailbox))
            tracing.record_output(span, asdict(result))
            return result

    async def execute(self, job: FollowupJob) -> str:
        evidence = await self.read(job)
        if (evidence.mailbox != job.first.mailbox or not evidence.items or evidence.truncated
                or any(m.unread_attachments or m.thread_id != job.first.thread_id for m in evidence.items)):
            reason = 'The latest email evidence is incomplete. Please review it before more messages are sent.'
            await self.store.pause(job, reason)
            return reason
        if job.count == 0:
            policy = await CaseFollowup.objects.aget(pk=job.identifier)
            export = await GmailDraftExport.objects.aget(draft__case_id=job.case_id, draft__version=policy.draft_version)
            with self.mailbox() as mailbox:
                current_draft = await sync_to_async(mailbox.read_draft)(export.gmail_id, job.first.mailbox)
            if current_draft != job.first:
                reason = 'The approved draft changed before sending. Please review it and approve a fresh plan.'
                await self.store.pause(job, reason)
                return reason
            if set(job.baseline) != {m.message_id for m in evidence.items}:
                reason = 'The thread changed after the sending plan was prepared. Please review it and approve a fresh plan.'
                await self.store.pause(job, reason)
                return reason
            envelope = job.first
        else:
            async with self.model_session() as model:
                agent: Agent[None, FollowupDecision] = Agent(model, output_type=FollowupDecision,
                    name='gmail_followup_writer', instructions=FOLLOWUP_INSTRUCTIONS,
                    retries=constants.PLANNER_RETRIES, model_settings={'max_tokens': MAX_DECISION_TOKENS})
                agent.instrument = tracing.private_instrumentation()
                decision = (await agent.run(json.dumps({'approved_first_message': asdict(job.first),
                    'approved_baseline_message_ids': job.baseline, 'followup_number': job.count, 'maximum_followups': job.maximum, 'is_final_followup': job.count == job.maximum, 'sent_messages': job.sent,
                    'current_time': self.clock().isoformat(), 'correspondence': [m.evidence() for m in evidence.items]}, default=str),
                    timeout=constants.ASSISTANCE_TIMEOUT_SECONDS)).output
            if decision.action == 'pause' or not decision.body.strip() or len(decision.body.split()) > MAX_BODY_WORDS:
                await self.store.pause(job, decision.reason)
                return decision.reason
            envelope = replace(job.first, body=decision.body)
        # Model work can take time. Recheck the entire evidence, not just a cached classification.
        latest = await self.read(job)
        if latest != evidence:
            await self.store.pause(job, 'New correspondence arrived while preparing the email. Please review it.')
            return 'Sending paused because correspondence changed.'
        envelope = replace(envelope, marker=f'followup-{job.identifier}-{job.generation}-{job.count}')
        attempt = await self.store.reserve(job, envelope)
        if attempt is None:
            return 'Sending was cancelled or already attempted.'
        message_id = ''
        try:
            message_id = await drain_on_cancel(self.handoff(job, envelope))
        finally:
            await asyncio.shield(self.store.finish(job, attempt, message_id, self.clock()))
        return 'Email sent.' if message_id else 'Send outcome unconfirmed; no retry scheduled.'

    @sync_to_async
    def handoff(self, job: FollowupJob, envelope: DraftEnvelope) -> str:
        # The attempted record is already committed. The owner lock serializes this
        # external handoff with stop/deletion; a crash leaves an unrepeatable attempt.
        with transaction.atomic():
            if self.store.locked(job) is None:
                return ''
            with self.mailbox() as mailbox, tracing.operation('gmail.send', asdict(envelope), private=True) as span:
                draft_id = ''
                if job.count == 0:
                    policy = CaseFollowup.objects.get(pk=job.identifier)
                    export = GmailDraftExport.objects.get(draft__case_id=job.case_id, draft__version=policy.draft_version)
                    draft_id = export.gmail_id
                identifier = mailbox.send_message(envelope, draft_id)
                tracing.record_output(span, {'message_id': identifier, 'status': 'sent'})
                return identifier

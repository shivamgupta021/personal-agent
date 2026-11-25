"""Local scheduled monitoring; no autonomous drafts, sending, or case resolution."""
import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, AbstractAsyncContextManager
from datetime import datetime
from dataclasses import replace
import logging

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.tracing import tracing
from core.turns import OwnerTurn, PreparedReply
from gmail.mailbox import Mailbox
from gmail.errors import GmailBusy
from subscriptions.auth import SubscriptionBusy
from gmail.monitoring_constants import MAX_CASES_PER_TICK
from gmail.monitoring_store import MonitorJob, MonitoringStore
from gmail.reply_checks import GmailReplyChecks, ReplyCheckResult
from grievances.lifecycle import ApplicationTurn
from grievances.store import CaseStore
from pydantic_ai.models import Model

logger = logging.getLogger(__name__)


class MonitoringWorker:
    def __init__(self, model_session: Callable[[], AbstractAsyncContextManager[Model]],
                 mailbox: Callable[[], AbstractContextManager[Mailbox]],
                 send: Callable[[PreparedReply], Awaitable[None]],
                 *, clock: Callable[[], datetime] = timezone.now) -> None:
        self.model_session = model_session
        self.mailbox = mailbox
        self.send = send
        self.clock = clock
        self.store = MonitoringStore()

    async def tick(self, owner_id: str) -> None:
        """Public worker boundary; bounded catch-up, with durable leases and outbox."""
        await self.store.queue_due_reviews(owner_id, self.clock())
        await self.deliver_pending(owner_id)
        for _ in range(MAX_CASES_PER_TICK):
            job = await self.store.claim(owner_id, self.clock())
            if job is None:
                break
            turn = job.turn()
            try:
                await ApplicationTurn().prepare(turn, lambda request, epoch: self.check(job, request))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('Monitoring check unavailable; preserving state and backing off')
                await self.store.finish(job, ReplyCheckResult('Reply status unknown'), self.clock())
            await self.deliver_pending(owner_id)

    async def check(self, job: MonitorJob, turn: OwnerTurn) -> str:
        return (await self.check_result(job, turn)).answer

    async def check_result(self, job: MonitorJob, turn: OwnerTurn) -> ReplyCheckResult:
        with tracing.operation('gmail.monitor', {'case_id': job.case_id, 'thread_id': job.thread_id}, private=True) as span:
            if not await self.store.valid(job):
                tracing.record_output(span, {'status': 'cancelled'})
                return ReplyCheckResult('Monitoring was stopped or the case changed.')
            case_store = CaseStore()
            selection = await case_store.selection(turn.owner_id, turn.conversation_id)
            current = await case_store.get_case(turn.owner_id, job.case_id)
            if current is None:
                return ReplyCheckResult('Case is unavailable.')
            try:
                async with self.model_session() as model:
                    result = await GmailReplyChecks(model, self.mailbox).check(turn, selection, current, reuse_analysis=True)
            except (GmailBusy, SubscriptionBusy):
                await self.store.defer(job, self.clock())
                tracing.record_output(span, {'status': 'deferred', 'reason': 'foreground resource busy'})
                logger.info('Monitoring deferred while a foreground resource is busy')
                return ReplyCheckResult('Monitoring deferred while a foreground operation is running.')
            await self.store.finish(job, result, self.clock())
            tracing.record_output(span, {'verified': result.verified, 'answer': result.answer})
            return result

    async def deliver_pending(self, owner_id: str) -> None:
        for identifier, job, text in await self.store.pending(owner_id):
            try:
                needs_check, ready = await self.store.reminder_delivery_check(identifier, self.clock())
                if not ready or (needs_check and not await self.refresh_reminder(job)):
                    continue
                if not await self.valid_delivery(job, identifier):
                    continue
                # Persist BEFORE external handoff. A crash/timeout is ambiguous, never blindly resent.
                if not await self.store.handoff(identifier, job):
                    continue
                async def notification(turn: OwnerTurn, epoch: int) -> str:
                    return text

                traced = await ApplicationTurn().prepare(replace(job.turn(), text='Deliver scheduled case notification.'), notification)
                reply = PreparedReply(traced.text, lambda: self.valid_delivery(job, identifier), traced.trace_id)
                await self.send(reply)
                await self.store.delivered(identifier)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('Monitoring delivery unavailable; attempted handoffs will not be automatically resent')

    async def valid_delivery(self, job: MonitorJob, identifier: int) -> bool:
        if not await self.store.valid(job) or not await self.store.notification_valid(identifier):
            return False
        with self.mailbox() as mailbox:
            await sync_to_async(mailbox.verify_account)(job.mailbox)
        return await self.store.valid(job) and await self.store.notification_valid(identifier)


    async def refresh_reminder(self, job: MonitorJob) -> bool:
        verified = False

        async def execute(turn: OwnerTurn, epoch: int) -> str:
            nonlocal verified
            result = await self.check_result(job, turn)
            verified = result.verified
            return result.answer

        try:
            await ApplicationTurn().prepare(job.turn(), execute)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('Reminder recheck unavailable; preserving state and backing off')
            await self.store.finish(job, ReplyCheckResult('Reply status unknown'), self.clock())
            return False
        return verified

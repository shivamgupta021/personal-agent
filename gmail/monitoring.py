"""Local scheduled monitoring; no autonomous drafts, sending, or case resolution."""
import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, AbstractAsyncContextManager
from datetime import datetime
import logging

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.tracing import tracing
from core.turns import OwnerTurn, PreparedReply
from gmail.mailbox import Mailbox
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
        with tracing.operation('gmail.monitor', {'case_id': job.case_id, 'thread_id': job.thread_id}, private=True) as span:
            if not await self.store.valid(job):
                tracing.record_output(span, {'status': 'cancelled'})
                return 'Monitoring was stopped or the case changed.'
            case_store = CaseStore()
            selection = await case_store.selection(turn.owner_id, turn.conversation_id)
            current = await case_store.get_case(turn.owner_id, job.case_id)
            if current is None:
                return 'Case is unavailable.'
            async with self.model_session() as model:
                result = await GmailReplyChecks(model, self.mailbox).check(turn, selection, current, reuse_analysis=True)
            await self.store.finish(job, result, self.clock())
            tracing.record_output(span, {'verified': result.verified, 'answer': result.answer})
            return result.answer

    async def deliver_pending(self, owner_id: str) -> None:
        for identifier, job, text in await self.store.pending(owner_id):
            try:
                if not await self.valid_delivery(job, identifier):
                    continue
                # Persist BEFORE external handoff. A crash/timeout is ambiguous, never blindly resent.
                if not await self.store.handoff(identifier, job):
                    continue
                reply = PreparedReply(text, lambda: self.valid_delivery(job, identifier))
                await self.send(reply)
                await self.store.delivered(identifier)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('Monitoring notification unconfirmed; no automatic resend after handoff')

    async def valid_delivery(self, job: MonitorJob, identifier: int) -> bool:
        if not await self.store.valid(job) or not await self.store.notification_valid(identifier):
            return False
        with self.mailbox() as mailbox:
            await sync_to_async(mailbox.verify_account)(job.mailbox)
        return await self.store.valid(job) and await self.store.notification_valid(identifier)

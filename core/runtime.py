"""Subscription lifetime and resumable Gmail connection for application turns."""
import asyncio
from dataclasses import replace
from collections.abc import Awaitable, Callable
from django.conf import settings

from core.actions import CaseAction
from core.errors import user_error_message
from core.service import ApplicationService
from core.tracing import tracing
from core.turns import OwnerTurn, PreparedReply
from gmail.correspondence import Correspondence, CONSENT_MESSAGE
from gmail.factory import GmailFactory
from gmail.onboarding import GmailConnectionRequested, ConnectionAttempt
from gmail.planner import GmailQueryPlanner
from core.lifecycle import ApplicationTurn
from grievances.ownership import CaseOwnership
from grievances.store import CaseStore
from subscriptions.service import SubscriptionService


class PublicResearchRequested(Exception):
    """Unwind the private subscription session before entering public research."""


class SubscriptionApplicationService:
    """Open the subscription transport for one normalized application turn."""

    def __init__(self, subscription: SubscriptionService, public_run: Callable[[str], Awaitable[str]],
                 connect_gmail: Callable[[OwnerTurn, str, bool], Awaitable[ConnectionAttempt]] | None = None,
                 progress: Callable[[PreparedReply], Awaitable[None]] | None = None) -> None:
        self.connection_task: asyncio.Task[None] | None = None
        self.progress = progress
        self.subscription = subscription
        self.connect_gmail = connect_gmail
        self.public_run = public_run

    async def run(self, turn: OwnerTurn) -> str:
        return await (await self.prepare(turn)).render()

    async def prepare(self, turn: OwnerTurn) -> PreparedReply:
        return await ApplicationTurn().prepare(turn, self._execute)

    async def _execute(self, turn: OwnerTurn, epoch: int) -> str:
        if ApplicationService.is_memory_control(turn.text):
            return await self.public_run(turn.text)
        try:
            async with self.subscription.model() as model:
                return await ApplicationService(model, self.defer_public,
                    correspondence=Correspondence(GmailFactory.mailbox, GmailQueryPlanner(model), GmailFactory.require_connection),
                    progress=self.progress, routine_tools=settings.ROUTINE_TOOLS_ENABLED)._execute(turn, epoch)
        except GmailConnectionRequested as request:
            if self.connect_gmail is None:
                return 'Gmail connection setup is available through the Telegram application on this Mac.'
            if self.progress is not None:
                if self.connection_task is not None and not self.connection_task.done():
                    return 'Gmail consent is already open on this Mac. Complete it there; I’ll continue automatically.'
                self.connection_task = asyncio.create_task(self._connect_in_background(replace(turn.followup(), callback=turn.callback), request, epoch))
                return 'I’m opening Gmail consent on this Mac. You can keep chatting while I wait; I’ll continue your case when access is ready.'
            return await self._connect_and_resume(turn, request, epoch)
        except PublicResearchRequested:
            if epoch != await CaseOwnership().epoch(turn.owner_id):
                return 'Case content was deleted. Please send a new public request.'
            with tracing.public_content():
                return await self.public_run(turn.text)

    async def _connect_and_resume(self, turn: OwnerTurn, request: GmailConnectionRequested, epoch: int) -> str:
        if self.connect_gmail is None:
            return 'Gmail connection is unavailable.'
        original_selection = await CaseStore().selection(turn.owner_id, turn.conversation_id)
        connected = await self.connect_gmail(turn, request.email, request.compose)
        if not connected.connected:
            return connected.message
        if epoch != await CaseOwnership().epoch(turn.owner_id):
            return connected.message + ' The case changed; please send a new request.'
        selection = await CaseStore().selection(turn.owner_id, turn.conversation_id)
        if selection.case_id != original_selection.case_id:
            return connected.message + ' Your selected case changed; tell me which case to continue.'
        if turn.callback and CaseAction.verify(turn.callback, turn.owner_id, epoch):
            await Correspondence.accept(turn.owner_id)
        pending = await Correspondence().pending_goal(turn)
        if pending and await Correspondence.consented(turn.owner_id):
            resumed = await self._execute(replace(turn.followup(), text=pending, callback=''), epoch)
            return resumed
        if pending:
            return connected.message + '\n\n' + CONSENT_MESSAGE
        return connected.message + ' Tell me which email or case you need help with.'

    async def _connect_in_background(self, turn: OwnerTurn, request: GmailConnectionRequested, epoch: int) -> None:
        async def complete(current: OwnerTurn, current_epoch: int) -> str:
            if current_epoch != epoch:
                return 'The case changed before Gmail connection started. Please send a new request.'
            try:
                return await self._connect_and_resume(current, request, epoch)
            except Exception as error:
                return user_error_message(error) + ' Please send your request again.'

        reply = await ApplicationTurn().prepare(turn, complete)
        if self.progress is not None:
            await self.progress(reply)

    async def finish_connections(self) -> None:
        if self.connection_task is not None:
            await self.connection_task

    async def close(self) -> None:
        if self.connection_task is not None:
            self.connection_task.cancel()
            await asyncio.gather(self.connection_task, return_exceptions=True)

    async def defer_public(self, message: str) -> str:
        raise PublicResearchRequested

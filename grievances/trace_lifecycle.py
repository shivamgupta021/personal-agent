"""Own application trace producers until all captured content can be erased."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

import httpx2
from opentelemetry.trace import Span

from conversations.cleanup import TraceCleanup
from conversations.store import ConversationStore
from core.tracing import tracing
from core.turns import OwnerTurn
from grievances.cleanup import CaseTraceCleanup, CaseTraceRegistry

logger = logging.getLogger(__name__)


class ApplicationTrace:
    def __init__(self, turn: OwnerTurn, cleanup: CaseTraceCleanup | None = None) -> None:
        self.turn = turn
        self.cleanup = cleanup
        self.registry = CaseTraceRegistry(cleanup)
        self.conversations = ConversationStore()

    @asynccontextmanager
    async def run(self) -> AsyncIterator[Span]:
        trace_id: str | None = None
        try:
            with tracing.application_producer(), tracing.operation('application.turn', {}, kind='agent', private=True, root=True) as span:
                span.set_attribute('langfuse.session.id', self.turn.owner_id)
                if span.is_recording():
                    trace_id = format(span.get_span_context().trace_id, '032x')
                    # Associate before any content is captured, including routing preferences
                    # and requests that never reach the conversation runner.
                    await self.conversations.track_application(self.turn.owner_id, trace_id)
                    await self.registry.register_existing(self.turn.owner_id)
                yield span
        finally:
            if trace_id is not None:
                completion = asyncio.create_task(self._finish(trace_id))
                try:
                    await asyncio.shield(completion)
                except asyncio.CancelledError:
                    await completion
                    raise

    async def _finish(self, trace_id: str) -> None:
        # The outer root and every child producer have ended before this flush.
        await self.registry.close(self.turn.owner_id, trace_id)
        if await self.conversations.trace_pending(self.turn.owner_id, trace_id):
            try:
                if self.cleanup is not None:
                    await TraceCleanup(self.cleanup.client).run(self.turn.owner_id)
                else:
                    await TraceCleanup.configured(self.turn.owner_id)
            except (httpx2.HTTPError, ValueError):
                logger.warning('Conversation trace cleanup pending after application turn')

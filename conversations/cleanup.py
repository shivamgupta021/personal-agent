"""Retry trace deletion without ever carrying conversation content."""

import asyncio

import httpx2
from django.conf import settings

from conversations.store import ConversationStore
from core.tracing import tracing


class TraceCleanup:
    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client
        self.store = ConversationStore()

    async def run(self, identity: str) -> str:
        pending = await self.store.pending(identity)
        if not pending:
            return 'No trace cleanup remains.'
        if tracing.application_producer_active():
            return 'Trace cleanup pending until this application turn finishes.'
        if not await asyncio.to_thread(tracing.flush):
            return 'Trace export is still pending. Say “retry trace cleanup” later.'
        failed = False
        for trace_id in pending:
            try:
                response = await self.client.delete(f'/api/public/traces/{trace_id}')
                if response.status_code not in (200, 202, 204, 404):
                    failed = True
            except httpx2.HTTPError:
                failed = True
        # Langfuse deletion is asynchronous. Retain the IDs for safe repeated deletion;
        # an accepted request or a not-yet-ingested 404 is not proof of erasure.
        if failed:
            return 'Some trace cleanup requests failed. Say “retry trace cleanup” later; local history stays deleted.'
        return 'Trace deletion requested; Langfuse processes it asynchronously. Say “retry trace cleanup” to repeat cleanup.'

    @classmethod
    async def configured(cls, identity: str) -> str:
        async with httpx2.AsyncClient(
            base_url=settings.LANGFUSE_BASE_URL,
            auth=(settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY),
            timeout=settings.LANGFUSE_TIMEOUT_SECONDS, follow_redirects=False,
        ) as client:
            return await cls(client).run(identity)

"""Case trace ownership and bounded, verified remote cleanup."""

from __future__ import annotations

import asyncio
import re
from uuid import UUID

import httpx2
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from opentelemetry.trace import get_current_span

from core.tracing import tracing
from grievances.constants import TRACE_CLEANUP_ABSENCE_CONFIRMATIONS, TRACE_CLEANUP_BATCH_SIZE, TRACE_CLEANUP_INTERVAL_SECONDS, TRACE_CLEANUP_MAX_PAGES, TRACE_CLEANUP_POLLS, TRACE_CLEANUP_TIMEOUT_SECONDS
from grievances.models import CaseTrace, GrievanceCase
from grievances.ownership import CaseOwnership


class CaseTraceRegistry:
    def __init__(self, cleanup: CaseTraceCleanup | None = None) -> None:
        self.cleanup = cleanup

    @sync_to_async
    def register(self, owner_id: str, case_id: str, trace_id: str) -> bool:
        if not re.fullmatch('[0-9a-f]{32}', trace_id):
            return False
        try:
            identifier = UUID(case_id)
        except ValueError:
            return False
        with transaction.atomic():
            CaseOwnership.lock(owner_id)
            if not GrievanceCase.objects.filter(owner_id=owner_id, pk=identifier).exists():
                return False
            row, created = CaseTrace.objects.get_or_create(trace_id=trace_id, owner_id=owner_id, case_id=identifier)
            return created

    @staticmethod
    def register_current_in_transaction(owner_id: str, case_id: str) -> None:
        """Associate content while its owner's mutation lock is still held."""
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('Trace association requires the case transaction')
        span = get_current_span()
        if span.is_recording():
            CaseTrace.objects.get_or_create(owner_id=owner_id, case_id=UUID(case_id),
                trace_id=format(span.get_span_context().trace_id, '032x'))

    @sync_to_async
    def register_existing(self, owner_id: str) -> None:
        with transaction.atomic():
            CaseOwnership.lock(owner_id)
            for identifier in GrievanceCase.objects.filter(owner_id=owner_id).values_list('pk', flat=True):
                self.register_current_in_transaction(owner_id, str(identifier))

    async def close(self, owner_id: str, trace_id: str) -> None:
        # The process owning the producer must flush after its span has ended.
        # A later cleanup process cannot flush this process's exporter queue.
        if await asyncio.to_thread(tracing.flush):
            await self._closed(owner_id, trace_id)
            if await CaseTrace.objects.filter(owner_id=owner_id, trace_id=trace_id, pending=True).aexists():
                try:
                    if self.cleanup:
                        await self.cleanup.run(owner_id)
                    else:
                        await CaseTraceCleanup.configured(owner_id)
                except (httpx2.HTTPError, ValueError):
                    pass

    @sync_to_async
    def _closed(self, owner_id: str, trace_id: str) -> None:
        with transaction.atomic():
            CaseOwnership.lock(owner_id)
            CaseTrace.objects.filter(owner_id=owner_id, trace_id=trace_id).update(producer_closed=True)

    @sync_to_async
    def pending(self, owner_id: str) -> list[str]:
        return list(CaseTrace.objects.filter(owner_id=owner_id, pending=True).values_list('trace_id', flat=True).distinct())


class CaseTraceCleanup:
    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def _present(self, trace_id: str) -> bool | None:
        cursor = ''
        seen: set[str] = set()
        for _ in range(TRACE_CLEANUP_MAX_PAGES):
            params = {'traceId': trace_id, 'fields': 'core', 'limit': '1'}
            if cursor:
                params['cursor'] = cursor
            response = await self.client.get('/api/public/v2/observations', params=params)
            if response.status_code != 200:
                return None
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get('data'), list):
                return None
            if body['data']:
                return True
            meta = body.get('meta')
            if not isinstance(meta, dict):
                return None
            following = meta.get('cursor')
            if following is None:
                return False
            if not isinstance(following, str) or not following or following in seen:
                return None
            seen.add(following)
            cursor = following
        return None

    async def run(self, owner_id: str) -> str:
        try:
            async with asyncio.timeout(TRACE_CLEANUP_TIMEOUT_SECONDS):
                return await self._run(owner_id)
        except TimeoutError:
            return 'Case deleted locally; trace cleanup pending. Say “retry case trace cleanup” later.'

    async def _run(self, owner_id: str) -> str:
        pending = [row async for row in CaseTrace.objects.filter(owner_id=owner_id, pending=True).order_by('pk')[:TRACE_CLEANUP_BATCH_SIZE]]
        if not pending:
            return 'No case trace cleanup remains.'
        if not await asyncio.to_thread(tracing.flush):
            return 'Case deleted locally; trace cleanup pending export. Retry case trace cleanup later.'
        visited: set[str] = set()
        for row in pending:
            if row.trace_id in visited:
                continue
            visited.add(row.trace_id)
            if not row.producer_closed:
                continue
            try:
                present = await self._present(row.trace_id)
                if present:
                    row.observed = True
                    await CaseTrace.objects.filter(owner_id=owner_id, trace_id=row.trace_id).aupdate(observed=True)
                # A pre-ingestion absence is never proof that captured content was erased.
                if not row.observed:
                    continue
                response = await self.client.delete(f'/api/public/traces/{row.trace_id}')
                if response.status_code not in (200, 202, 204, 404):
                    continue
                absent = 0
                for _ in range(TRACE_CLEANUP_POLLS):
                    await asyncio.sleep(TRACE_CLEANUP_INTERVAL_SECONDS)
                    present = await self._present(row.trace_id)
                    absent = absent + 1 if present is False else 0
                    if absent >= TRACE_CLEANUP_ABSENCE_CONFIRMATIONS:
                        row.pending, row.confirmed = False, True
                        await CaseTrace.objects.filter(owner_id=owner_id, trace_id=row.trace_id).aupdate(pending=False, confirmed=True)
                        break
            except (httpx2.HTTPError, ValueError, TypeError):
                continue
        if await CaseTrace.objects.filter(owner_id=owner_id, pending=True).aexists():
            return 'Case deleted locally; trace cleanup pending. Say “retry case trace cleanup” later.'
        return 'Case deleted locally; registered trace cleanup confirmed by repeated observation absence after export and deletion.'

    @classmethod
    async def configured(cls, owner_id: str) -> str:
        async with httpx2.AsyncClient(base_url=settings.LANGFUSE_BASE_URL,
                auth=(settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY),
                timeout=settings.LANGFUSE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            return await cls(client).run(owner_id)

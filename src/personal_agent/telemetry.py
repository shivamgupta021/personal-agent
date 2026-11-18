"""One app-owned Langfuse connection, with separate metadata for every async run."""

import asyncio
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx2
from pydantic_ai.usage import RunUsage

from personal_agent import constants
from personal_agent.enums import RunStatus, ToolName
from personal_agent.errors import failure_status

logger = logging.getLogger(__name__)


@dataclass
class RunTrace:
    usage: RunUsage = field(default_factory=RunUsage)
    tools: list[ToolName] = field(default_factory=list)
    status: RunStatus = RunStatus.OK
    start: int = field(default_factory=time.time_ns)


class Tracing:
    def __init__(self) -> None:
        self._client: httpx2.AsyncClient | None = None
        self._started = False
        self._current: ContextVar[RunTrace | None] = ContextVar('research_trace', default=None)

    @asynccontextmanager
    async def startup(self, client: httpx2.AsyncClient | None = None) -> AsyncIterator[None]:
        """Initialize once for the app lifetime; an injected HTTP client supports integration tests."""
        if self._started:
            yield
            return
        owns_client = client is None
        if client is None:
            public_key = os.getenv('LANGFUSE_PUBLIC_KEY')
            secret_key = os.getenv('LANGFUSE_SECRET_KEY')
            if bool(public_key) != bool(secret_key):
                raise RuntimeError('Set both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.')
            if public_key and secret_key:
                client = httpx2.AsyncClient(
                    base_url=os.getenv('LANGFUSE_BASE_URL', constants.LANGFUSE_BASE_URL),
                    auth=(public_key, secret_key), follow_redirects=False,
                )
        self._client = client
        self._started = True
        logger.info('Langfuse metadata tracing %s', 'enabled' if client else 'disabled (no project keys)')
        try:
            yield
        finally:
            self._client = None
            self._started = False
            if owns_client and client is not None:
                await client.aclose()

    @asynccontextmanager
    async def observe(self) -> AsyncIterator[RunTrace]:
        """Record run timing/status here; never capture arguments, answers, or exception text."""
        trace = RunTrace()
        token = self._current.set(trace)
        logger.info('Research started')
        try:
            yield trace
        except BaseException as error:
            trace.status = failure_status(error)
            raise
        finally:
            self._current.reset(token)
            logger.info('Research finished: status=%s requests=%d tool_calls=%d',
                        trace.status, trace.usage.requests, trace.usage.tool_calls)
            if self._client is not None:
                await self._export(trace, time.time_ns())

    def record_tool(self, name: ToolName) -> None:
        trace = self._current.get()
        if trace is not None:
            trace.tools.append(name)
        logger.info('Tool started: %s', name)

    async def _export(self, trace: RunTrace, end: int) -> None:
        client = self._client
        if client is None:
            return
        attributes = [
            {'key': 'agent.status', 'value': {'stringValue': trace.status.value}},
            {'key': 'agent.tool_names', 'value': {'stringValue': ','.join(trace.tools)}},
            {'key': 'agent.requests', 'value': {'intValue': str(trace.usage.requests)}},
            {'key': 'agent.tool_calls', 'value': {'intValue': str(trace.usage.tool_calls)}},
            {'key': 'gen_ai.usage.input_tokens', 'value': {'intValue': str(trace.usage.input_tokens)}},
            {'key': 'gen_ai.usage.output_tokens', 'value': {'intValue': str(trace.usage.output_tokens)}},
        ]
        payload: dict[str, Any] = {'resourceSpans': [{
            'resource': {'attributes': [{'key': 'service.name', 'value': {'stringValue': 'personal-agent'}}]},
            'scopeSpans': [{'scope': {'name': 'personal-agent'}, 'spans': [{
                'traceId': uuid.uuid4().hex, 'spanId': uuid.uuid4().hex[:16], 'name': 'research',
                'kind': 1, 'startTimeUnixNano': str(trace.start), 'endTimeUnixNano': str(end),
                'attributes': attributes, 'status': {'code': 1 if trace.status == RunStatus.OK else 2},
            }]}],
        }]}
        try:
            # A whole-export deadline also bounds servers that slowly stream their response.
            async with asyncio.timeout(constants.TRACE_TIMEOUT_SECONDS):
                response = await client.post('/api/public/otel/v1/traces', json=payload,
                                             headers={'x-langfuse-ingestion-version': '4'},
                                             timeout=constants.TRACE_TIMEOUT_SECONDS)
                response.raise_for_status()
                result = response.json() if response.content else {}
                if result.get('partialSuccess', {}).get('rejectedSpans', 0) not in (0, '0'):
                    raise ValueError('rejected')
            logger.info('Trace metadata exported')
        except (httpx2.HTTPError, TimeoutError, ValueError, AttributeError):
            logger.warning('Trace export unavailable; research result is unaffected')


# Importing the service does no IO. ResearchApp starts and closes its connection once.
tracing = Tracing()

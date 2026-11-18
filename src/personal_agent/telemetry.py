"""Explicit metadata-only OTLP export to Langfuse; no auto-instrumentation."""

import uuid
from typing import Literal

import httpx2
from pydantic_ai.usage import RunUsage


class LangfuseTrace:
    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def send(self, start: int, end: int, usage: RunUsage,
                   tools: list[str], status: Literal['ok', 'timeout', 'limit', 'error']) -> None:
        # This method never receives messages, tool arguments/results, or exceptions.
        attributes = [
            {'key': 'agent.status', 'value': {'stringValue': status}},
            {'key': 'agent.tool_names', 'value': {'stringValue': ','.join(
                name for name in tools if name in {'search', 'read_page'})}},
            {'key': 'agent.requests', 'value': {'intValue': str(usage.requests)}},
            {'key': 'agent.tool_calls', 'value': {'intValue': str(usage.tool_calls)}},
            {'key': 'gen_ai.usage.input_tokens', 'value': {'intValue': str(usage.input_tokens)}},
            {'key': 'gen_ai.usage.output_tokens', 'value': {'intValue': str(usage.output_tokens)}},
        ]
        payload = {'resourceSpans': [{
            'resource': {'attributes': [{'key': 'service.name', 'value': {'stringValue': 'personal-agent'}}]},
            'scopeSpans': [{'scope': {'name': 'personal-agent'}, 'spans': [{
                'traceId': uuid.uuid4().hex, 'spanId': uuid.uuid4().hex[:16], 'name': 'research',
                'kind': 1, 'startTimeUnixNano': str(start), 'endTimeUnixNano': str(end),
                'attributes': attributes, 'status': {'code': 1 if status == 'ok' else 2},
            }]}],
        }]}
        try:
            response = await self.client.post('/api/public/otel/v1/traces', json=payload,
                                             headers={'x-langfuse-ingestion-version': '4'}, timeout=5)
            response.raise_for_status()
            result = response.json() if response.content else {}
            if result.get('partialSuccess', {}).get('rejectedSpans', 0) not in (0, '0'):
                raise ValueError('rejected')
            print('[trace] Metadata exported.', flush=True)
        except (httpx2.HTTPError, ValueError, AttributeError):
            print('[trace] Export unavailable; research result is unaffected.', flush=True)

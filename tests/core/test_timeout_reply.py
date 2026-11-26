import asyncio

import pytest
from django.test import override_settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.service import ApplicationService
from core.tracing import tracing
from core.turns import OwnerTurn


@pytest.mark.django_db(transaction=True)
def test_timed_out_turn_returns_safe_reply_with_original_trace() -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise TimeoutError('private provider payload')

    exporter = InMemorySpanExporter()
    with override_settings(LANGFUSE_CAPTURE_CONTENT=True):
        tracing.configure(exporter)
        try:
            reply = asyncio.run(ApplicationService(FunctionModel(respond)).prepare(
                OwnerTurn('timeout-owner', 'chat', 'timeout', 'Update my purchase date.')))
            assert reply.trace_id
            assert 'timed out' in reply.text
            assert 'Research' not in reply.text
            assert 'private provider' not in reply.text
            spans = exporter.get_finished_spans()
            root = next(s for s in spans if s.name == 'application.turn')
            assert root.status.is_ok is False
            assert format(root.context.trace_id, '032x') == reply.trace_id
        finally:
            tracing.shutdown()

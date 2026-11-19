"""Configure native Pydantic AI instrumentation and a standard Langfuse OTLP exporter once."""

import base64
import logging
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from opentelemetry.trace import NoOpTracerProvider, Span, StatusCode

from django.conf import settings
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

logger = logging.getLogger(__name__)
_private_content: ContextVar[bool] = ContextVar('private_trace_content', default=False)


class Tracing:
    def __init__(self) -> None:
        self._provider: TracerProvider | None = None

    def configure(self, exporter: SpanExporter | None = None) -> None:
        if self._provider is not None:
            return
        if exporter is None:
            if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
                Agent.instrument_all(False)
                logger.info('Tracing disabled: configure both Langfuse project keys to enable it')
                return
            credentials = f'{settings.LANGFUSE_PUBLIC_KEY}:{settings.LANGFUSE_SECRET_KEY}'.encode()
            exporter = OTLPSpanExporter(
                endpoint=f'{settings.LANGFUSE_BASE_URL.rstrip("/")}/api/public/otel/v1/traces',
                headers={'Authorization': 'Basic ' + base64.b64encode(credentials).decode(),
                         'x-langfuse-ingestion-version': '4'},
                timeout=settings.LANGFUSE_TIMEOUT_SECONDS,
            )
        self._provider = TracerProvider(
            resource=Resource.create({'service.name': 'personal-agent'}), shutdown_on_exit=False,
        )
        self._provider.add_span_processor(BatchSpanProcessor(exporter))
        Agent.instrument_all(InstrumentationSettings(
            tracer_provider=self._provider,
            include_content=settings.LANGFUSE_CAPTURE_CONTENT,
            include_binary_content=False,
            include_model_request_parameters=False,
        ))
        logger.info('Native Pydantic AI tracing enabled; content capture %s',
                    'enabled' if settings.LANGFUSE_CAPTURE_CONTENT else 'disabled')

    def private_instrumentation(self) -> InstrumentationSettings:
        """Per-agent privacy policy independent of public debugging configuration."""
        return InstrumentationSettings(tracer_provider=self._provider or NoOpTracerProvider(),
            include_content=False, include_binary_content=False, include_model_request_parameters=False)

    @contextmanager
    def operation(self, name: str, inputs: dict[str, Any], *, kind: str = 'retriever', private: bool = False) -> Iterator[Span]:
        provider = self._provider or NoOpTracerProvider()
        with provider.get_tracer('personal-agent.retrieval').start_as_current_span(
            name, record_exception=False, set_status_on_exception=False,
            attributes={'langfuse.observation.type': kind},
        ) as span:
            token = _private_content.set(private or _private_content.get())
            if settings.LANGFUSE_CAPTURE_CONTENT and not _private_content.get():
                span.set_attribute('langfuse.observation.input', json.dumps(inputs))
            try:
                yield span
            except BaseException:
                span.set_status(StatusCode.ERROR)
                raise
            finally:
                _private_content.reset(token)

    def record_output(self, span: Span, output: dict[str, Any]) -> None:
        if settings.LANGFUSE_CAPTURE_CONTENT and not _private_content.get():
            span.set_attribute('langfuse.observation.output', json.dumps(output))
        if 'error' in output:
            span.set_status(StatusCode.ERROR)

    def flush(self) -> bool:
        if self._provider is not None:
            try:
                if not self._provider.force_flush(timeout_millis=int(settings.LANGFUSE_TIMEOUT_SECONDS * 1000)):
                    logger.warning('Trace export did not finish before its deadline')
                    return False
            except Exception:
                logger.warning('Trace export unavailable; application result is unaffected')
                return False
        return True

    def shutdown(self) -> None:
        if self._provider is not None:
            provider, self._provider = self._provider, None
            Agent.instrument_all(False)
            provider.shutdown()


tracing = Tracing()

"""Export-time email exclusion, including native model spans and exception events."""
from collections.abc import Sequence
from contextvars import ContextVar

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status

email_private: ContextVar[bool] = ContextVar('email_private', default=False)


class EmailMarker(SpanProcessor):
    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        if email_private.get():
            span.set_attribute('email.content_omitted', True)


class EmailSafeExporter(SpanExporter):
    SAFE_KEYS = frozenset(('langfuse.observation.type', 'gen_ai.operation.name',
        'gen_ai.request.model', 'gen_ai.response.model', 'gen_ai.system', 'gen_ai.provider.name',
        'langfuse.session.id', 'application.route', 'email.content_omitted',
        'gmail.results', 'gmail.messages', 'gmail.truncated', 'gmail.query_count'))

    def __init__(self, exporter: SpanExporter) -> None:
        self.exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self.exporter.export([self._safe(span) for span in spans])

    def _safe(self, span: ReadableSpan) -> ReadableSpan:
        attributes = span.attributes or {}
        if not attributes.get('email.content_omitted'):
            return span
        safe = {key: value for key, value in attributes.items()
                if key in self.SAFE_KEYS or key.startswith('gen_ai.usage.')}
        safe['langfuse.observation.input'] = '{"omitted":"email privacy"}'
        safe['langfuse.observation.output'] = '{"omitted":"email privacy"}'
        return ReadableSpan(name=span.name, context=span.context, parent=span.parent,
            resource=span.resource, attributes=safe, events=(), links=(), kind=span.kind,
            status=Status(span.status.status_code), start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope)

    def shutdown(self) -> None:
        self.exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.exporter.force_flush(timeout_millis)

"""Atomic persistence; failed model runs never overwrite completed context."""

from typing import Any

from asgiref.sync import sync_to_async
from django.db import transaction
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from conversations.models import ConversationRecord, ConversationTrace, ConversationSummary
from conversations.context import ConversationContext


class ContextChanged(Exception):
    pass


class ConversationStore:
    @sync_to_async
    def context(self, identity: str) -> ConversationContext:
        record, _ = ConversationRecord.objects.get_or_create(identity=identity)
        return ConversationContext(ModelMessagesTypeAdapter.validate_python(record.history), record_id=record.pk, revision=record.revision)

    @sync_to_async
    def archive(self, identity: str) -> dict[str, Any]:
        record = ConversationRecord.objects.filter(identity=identity).only('archive').first()
        return dict(record.archive) if record else {}

    @sync_to_async
    def save(self, identity: str, messages: list[ModelMessage], context: ConversationContext) -> None:
        with transaction.atomic():
            record = ConversationRecord.objects.select_for_update().filter(identity=identity, pk=context.record_id).first()
            if record is None or record.revision != context.revision:
                raise ContextChanged
            archive = dict(record.archive)
            # Existing pre-compaction records are captured on their first continued run.
            archive.update({key: ModelMessagesTypeAdapter.dump_python([message], mode='json')[0]
                            for key, message in context.originals.items()})
            record.archive = archive
            record.history = ModelMessagesTypeAdapter.dump_python(messages, mode='json')
            record.revision += 1
            record.save(update_fields=['archive', 'history', 'revision'])
            for summary in context.summaries:
                ConversationSummary.objects.create(conversation=record,
                    snapshot=ModelMessagesTypeAdapter.dump_python([summary], mode='json')[0])

    @sync_to_async
    def replace_facts(self, identity: str, context: ConversationContext,
                      originals: list[ModelMessage], history: list[ModelMessage]) -> None:
        with transaction.atomic():
            record = ConversationRecord.objects.select_for_update().filter(identity=identity, pk=context.record_id).first()
            if record is None or record.revision != context.revision:
                raise ContextChanged
            encoded = ModelMessagesTypeAdapter.dump_python(originals, mode='json')
            record.archive = {message['metadata']['archive_id']: message for message in encoded}
            record.history = ModelMessagesTypeAdapter.dump_python(history, mode='json')
            record.revision += 1
            record.save(update_fields=['archive', 'history', 'revision'])
            ConversationSummary.objects.filter(conversation=record).delete()
            # Every prior turn may have carried the selected fact in its context.
            ConversationTrace.objects.filter(identity=identity).update(pending_deletion=True)

    @sync_to_async
    def track(self, identity: str, trace_id: str, context: ConversationContext) -> None:
        with transaction.atomic():
            record = ConversationRecord.objects.select_for_update().filter(identity=identity, pk=context.record_id).first()
            stale = record is None or record.revision != context.revision
            trace, _ = ConversationTrace.objects.get_or_create(identity=identity, trace_id=trace_id)
            if stale:
                trace.pending_deletion = True
                trace.save(update_fields=['pending_deletion'])
        if stale:
            raise ContextChanged

    @sync_to_async
    def forget(self, identity: str) -> None:
        with transaction.atomic():
            ConversationRecord.objects.filter(identity=identity).delete()
            ConversationTrace.objects.filter(identity=identity).update(pending_deletion=True)

    @sync_to_async
    def trace_pending(self, identity: str, trace_id: str) -> bool:
        return ConversationTrace.objects.filter(identity=identity, trace_id=trace_id, pending_deletion=True).exists()

    @sync_to_async
    def pending(self, identity: str) -> list[str]:
        return list(ConversationTrace.objects.filter(identity=identity, pending_deletion=True).values_list('trace_id', flat=True))


"""Atomic persistence; failed model runs never overwrite completed context."""

from typing import Any

from asgiref.sync import sync_to_async
from django.db import transaction
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from conversations.models import ConversationRecord, ConversationTrace, ConversationSummary
from conversations.context import ConversationContext


class ConversationStore:
    @sync_to_async
    def history(self, identity: str) -> list[ModelMessage]:
        record, _ = ConversationRecord.objects.get_or_create(identity=identity)
        return ModelMessagesTypeAdapter.validate_python(record.history)

    @sync_to_async
    def archive(self, identity: str) -> dict[str, Any]:
        record = ConversationRecord.objects.filter(identity=identity).only('archive').first()
        return dict(record.archive) if record else {}

    @sync_to_async
    def save(self, identity: str, messages: list[ModelMessage], context: ConversationContext) -> None:
        with transaction.atomic():
            record = ConversationRecord.objects.select_for_update().get(identity=identity)
            archive = dict(record.archive)
            # Existing pre-compaction records are captured on their first continued run.
            archive.update({key: ModelMessagesTypeAdapter.dump_python([message], mode='json')[0]
                            for key, message in context.originals.items()})
            record.archive = archive
            record.history = ModelMessagesTypeAdapter.dump_python(messages, mode='json')
            record.save(update_fields=['archive', 'history'])
            for summary in context.summaries:
                ConversationSummary.objects.create(conversation=record,
                    snapshot=ModelMessagesTypeAdapter.dump_python([summary], mode='json')[0])

    @sync_to_async
    def track(self, identity: str, trace_id: str) -> None:
        ConversationTrace.objects.get_or_create(identity=identity, trace_id=trace_id)

    @sync_to_async
    def forget(self, identity: str) -> None:
        with transaction.atomic():
            ConversationRecord.objects.filter(identity=identity).delete()
            ConversationTrace.objects.filter(identity=identity).update(pending_deletion=True)

    @sync_to_async
    def pending(self, identity: str) -> list[str]:
        return list(ConversationTrace.objects.filter(identity=identity, pending_deletion=True).values_list('trace_id', flat=True))


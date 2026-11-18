"""Atomic persistence; failed model runs never overwrite completed context."""

from asgiref.sync import sync_to_async
from django.db import transaction
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from conversations.models import ConversationRecord, ConversationTrace


class ConversationStore:
    @sync_to_async
    def history(self, identity: str) -> list[ModelMessage]:
        record, _ = ConversationRecord.objects.get_or_create(identity=identity)
        return ModelMessagesTypeAdapter.validate_python(record.history)

    @sync_to_async
    def save(self, identity: str, messages: list[ModelMessage]) -> None:
        ConversationRecord.objects.update_or_create(
            identity=identity, defaults={'history': ModelMessagesTypeAdapter.dump_python(messages, mode='json')},
        )

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


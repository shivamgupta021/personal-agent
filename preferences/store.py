"""Preference persistence; source references point to editable conversation originals."""

from dataclasses import dataclass
from functools import partial
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.db import transaction
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from conversations.context import ConversationContext
from conversations.store import ConversationStore
from preferences.constants import MAX_PREFERENCES
from preferences.models import SavedPreference


@dataclass(frozen=True)
class Preference:
    key: str
    value: str
    source_message_id: str
    source_suggestion_id: str


class PreferenceStore:
    @sync_to_async
    def list(self, identity: str) -> list[Preference]:
        return [Preference(row.key, row.value, row.source_message_id, row.source_suggestion_id)
                for row in SavedPreference.objects.filter(identity=identity).order_by('key')]

    async def record_reply(self, identity: str, context: ConversationContext, request: str, reply: str,
                           *, key: str = '', value: str = '', suggestion_id: str = '') -> None:
        message_id = uuid4().hex
        messages: list[ModelMessage] = [*context.messages,
            ModelRequest(parts=[UserPromptPart(request)], metadata={'archive_id': message_id}),
            ModelResponse(parts=[TextPart(reply)], metadata={'archive_id': uuid4().hex})]
        context.capture(messages)
        mutation = partial(self.upsert_in_transaction, identity, key, value, message_id, suggestion_id) if key else None
        await ConversationStore().save(identity, messages, context, mutation=mutation)

    def upsert_in_transaction(self, identity: str, key: str, value: str, message_id: str, suggestion_id: str) -> None:
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('Preference changes require a transaction')
        existing = SavedPreference.objects.filter(identity=identity, key=key).exists()
        if not existing and SavedPreference.objects.filter(identity=identity).count() >= MAX_PREFERENCES:
            raise ValueError('Saved preference limit reached; forget an unused preference first.')
        SavedPreference.objects.update_or_create(identity=identity, key=key, defaults={
            'value': value, 'source_message_id': message_id, 'source_suggestion_id': suggestion_id})

    def delete_in_transaction(self, identity: str, key: str) -> None:
        """Called only inside the conversation's revision-checked cleanup transaction."""
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('Preference deletion requires an atomic context cleanup')
        SavedPreference.objects.filter(identity=identity, key=key).delete()

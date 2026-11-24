"""Read saved preferences without generation or mutation authorization."""

import asyncio

from conversations.store import ConversationStore, ContextChanged
from core.tracing import tracing
from core.turns import OwnerTurn
from preferences.constants import INSPECTION_PHRASES
from preferences.store import Preference, PreferenceStore


class PreferenceInspection:
    @staticmethod
    def matches(message: str) -> bool:
        # Match only a complete read request; mixed requests retain normal routing.
        return ' '.join(message.strip().rstrip('?.!').casefold().split()) in INSPECTION_PHRASES

    @staticmethod
    def render(preferences: list[Preference]) -> str:
        text = '\n'.join(f'{p.key.replace("_", " ")}: {p.value}' for p in preferences)
        return 'Saved preferences:\n' + text if text else 'No saved preferences.'

    async def run(self, turn: OwnerTurn) -> str:
        store = PreferenceStore()
        with tracing.operation('preferences.list', {'message': turn.text}, private=True) as span:
            context = await ConversationStore().context(turn.owner_id)
            preferences = await store.list(turn.owner_id)
            answer = self.render(preferences)
            commit = asyncio.create_task(store.record_reply(turn.owner_id, context, turn.text, answer))
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                await commit
                raise
            except ContextChanged:
                answer = 'Your saved context changed while I was checking. Please ask again.'
            span.set_attribute('preferences.count', len(preferences))
            tracing.record_output(span, {'answer': answer})
            return answer

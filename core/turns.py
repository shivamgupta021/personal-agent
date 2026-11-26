"""Transport-normalized authenticated input; delivery identities survive retries."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from attachments.records import Upload


@dataclass(frozen=True)
class OwnerTurn:
    owner_id: str
    conversation_id: str
    delivery_id: str
    text: str
    uploads: tuple[Upload, ...] = ()
    callback: str = ""

    def __post_init__(self) -> None:
        if not all((self.owner_id, self.conversation_id, self.delivery_id)):
            raise ValueError('Authenticated owner, conversation and delivery identities are required')
        if max(len(self.owner_id), len(self.conversation_id), len(self.delivery_id)) > 128:
            raise ValueError('Turn identity exceeds the supported length')


    def followup(self) -> 'OwnerTurn':
        """A bounded, stable generation phase for an already saved reply delivery."""
        return OwnerTurn(self.owner_id, self.conversation_id,
                         'followup:' + sha256(self.delivery_id.encode()).hexdigest(), self.text)


@dataclass(frozen=True)
class PreparedReply:
    """Response with a fresh authorization check at each external handoff."""
    text: str
    check: Callable[[], Awaitable[bool]]
    trace_id: str = ''

    async def valid(self) -> bool:
        return await self.check()

    async def render(self) -> str:
        return self.text if await self.valid() else 'Case content changed or was deleted; nothing was recreated. Please send a new request.'

"""Transport-normalized authenticated input; delivery identities survive retries."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OwnerTurn:
    owner_id: str
    conversation_id: str
    delivery_id: str
    text: str

    def __post_init__(self) -> None:
        if not all((self.owner_id, self.conversation_id, self.delivery_id)):
            raise ValueError('Authenticated owner, conversation and delivery identities are required')
        if max(len(self.owner_id), len(self.conversation_id), len(self.delivery_id)) > 128:
            raise ValueError('Turn identity exceeds the supported length')

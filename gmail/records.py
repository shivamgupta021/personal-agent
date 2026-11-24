"""Typed mailbox results and persisted correspondence snapshots."""
from dataclasses import asdict, dataclass, field
from typing import Any
from attachments.records import AttachmentText


@dataclass(frozen=True)
class DraftReference:
    draft_id: str
    message_id: str = ''


@dataclass(frozen=True)
class Candidate:
    thread_id: str
    message_id: str
    sender: str
    subject: str
    date: str
    preview: str
    reference: str = ''


@dataclass(frozen=True)
class ImportedMessage:
    message_id: str
    thread_id: str
    sender: str
    subject: str
    date: str
    text: str
    unread_attachments: bool
    labels: tuple[str, ...] = ()
    recipient: str = ''
    reply_to: str = ''
    rfc_message_id: str = ''
    references: str = ''
    attachment_results: list[AttachmentText] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'attachment_results', [AttachmentText(**item) if isinstance(item, dict) else item for item in self.attachment_results])

    def evidence(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop('attachment_results')  # Cache is storage metadata; text already contains bounded evidence.
        return result


@dataclass(frozen=True)
class MailResult[T]:
    mailbox: str
    items: list[T]
    truncated: bool = False


@dataclass(frozen=True)
class CorrespondenceSnapshot:
    mailbox: str = ''
    candidates: list[Candidate] = field(default_factory=list)
    selected_thread: str = ''
    messages: list[ImportedMessage] = field(default_factory=list)
    truncated: bool = False
    pending_goal: str = ''
    pending_question: str = ''
    pending_recipient: str = ''


@dataclass(frozen=True)
class DraftEnvelope:
    mailbox: str
    recipient: str
    subject: str
    body: str
    thread_id: str = ''
    reply_id: str = ''
    references: str = ''
    marker: str = ''

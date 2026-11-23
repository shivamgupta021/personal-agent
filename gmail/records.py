"""Typed mailbox results and persisted correspondence snapshots."""
from dataclasses import dataclass, field


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

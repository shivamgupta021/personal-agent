"""Extracted evidence; original bytes are never persisted or traced."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentText:
    name: str
    source: str
    text: str = ''
    status: str = 'unreadable'
    notice: str = ''
    digest: str = ''
    version: str = ''
    provider_key: str = ''

    def evidence(self) -> str:
        return f'Attachment {self.name} ({self.source}; {self.status}):\n{self.text}\n{self.notice}'


@dataclass(frozen=True)
class Upload:
    name: str
    mime: str
    data: bytes
    source: str

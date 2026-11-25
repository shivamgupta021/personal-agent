"""Render application Gmail links as Telegram actions, keeping other text intact."""

from dataclasses import dataclass
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ReplyPresentation:
    text: str
    buttons: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_text(cls, text: str) -> 'ReplyPresentation':
        lines: list[str] = []
        buttons: list[tuple[str, str]] = []
        for line in text.splitlines():
            link = re.fullmatch(r'\[([^\]\n]+)\]\((https://[^\s)]+)\)', line)
            if link and urlsplit(link[2]).netloc == 'mail.google.com':
                buttons.append((link[1], link[2]))
            else:
                lines.append(line)
        body = '\n'.join(lines).strip()
        return cls(body or 'Open in Gmail', tuple(dict.fromkeys(buttons)))

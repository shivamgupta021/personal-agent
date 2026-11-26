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
            link = re.fullmatch(r'\[([^\]\n]+)\]\(((?:https://|action://)[^\s)]+)\)', line)
            if link and (urlsplit(link[2]).netloc == 'mail.google.com' or link[2].startswith('action://')):
                buttons.append((link[1], link[2]))
            else:
                lines.append(line)
        body = '\n'.join(lines).strip()
        return cls(body or 'Open in Gmail', tuple(dict.fromkeys(buttons)))


    @staticmethod
    def formatted(text: str) -> dict[str, object]:
        """Send entity offsets rather than HTML so user text cannot inject tags."""
        output = ''
        entities: list[dict[str, object]] = []
        cursor = 0
        for match in re.finditer(r'\*\*([^*\n]+)\*\*|`([^`\n]+)`', text):
            output += text[cursor:match.start()]
            value = match[1] or match[2]
            entities.append({'type': 'bold' if match[1] else 'code',
                'offset': len(output.encode('utf-16-le')) // 2,
                'length': len(value.encode('utf-16-le')) // 2})
            output += value
            cursor = match.end()
        output += text[cursor:]
        return {'text': output, **({'entities': entities} if entities else {})}

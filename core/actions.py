"""Owner-bound, case-bound buttons; payloads confer no model-generated authority."""
from dataclasses import dataclass
import hashlib
import hmac
import re
from uuid import UUID

from django.conf import settings


@dataclass(frozen=True)
class CaseAction:
    kind: str
    case_id: str
    version: int = 0

    def token(self, owner: str, epoch: int) -> str:
        payload = f'{self.kind}:{UUID(self.case_id).hex}:{self.version:x}'
        signature = hmac.new(settings.SECRET_KEY.encode(),
            f'{owner}:{epoch}:{payload}'.encode(), hashlib.sha256).hexdigest()[:16]
        return f'{payload}:{signature}'

    def button(self, label: str, owner: str, epoch: int) -> str:
        return f'[{label}](action://{self.token(owner, epoch)})'

    @classmethod
    def verify(cls, token: str, owner: str, epoch: int) -> 'CaseAction | None':
        match = re.fullmatch(r'([gdpa]):([0-9a-f]{32}):([0-9a-f]{1,8}):([0-9a-f]{16})', token)
        if not match:
            return None
        action = cls(match[1], str(UUID(match[2])), int(match[3], 16))
        return action if hmac.compare_digest(action.token(owner, epoch), token) else None

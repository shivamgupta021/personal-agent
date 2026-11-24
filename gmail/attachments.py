"""Read selected-message attachments through Gmail's authenticated attachment API."""
import base64
from collections.abc import Callable
from dataclasses import replace
import hashlib
from typing import Any
from urllib.parse import quote

import httpx2

from attachments import constants
from attachments.records import AttachmentText
from attachments.service import AttachmentExtractor
from gmail.errors import GmailError
from gmail.constants import MAX_MIME_PARTS


class GmailAttachments:
    def __init__(self, get: Callable[..., dict[str, Any]]) -> None:
        self.get = get

    def read(self, payload: dict[str, Any], message_id: str, token: str,
             cached: list[AttachmentText], limit: int = constants.MAX_FILES) -> list[AttachmentText]:
        pending = [payload]
        results: list[AttachmentText] = []
        visited = 0
        while pending and visited < MAX_MIME_PARTS:
            part = pending.pop(0)
            visited += 1
            pending.extend(part.get('parts', [])[:MAX_MIME_PARTS])
            body = part.get('body', {})
            if not part.get('filename') and not body.get('attachmentId'):
                continue
            name = str(part.get('filename') or 'attachment')
            mime = str(part.get('mimeType', ''))
            reference = str(part.get('partId', visited))
            source = f'Gmail message {message_id}, part {reference}'
            if len(results) >= limit:
                results.append(AttachmentText(name, source, status='skipped', notice='Attachment reading limit reached for this thread.'))
                break
            if mime not in constants.SUPPORTED_TYPES or body.get('size', 0) > constants.MAX_FILE_BYTES:
                status = 'unsupported' if mime not in constants.SUPPORTED_TYPES else 'too_large'
                results.append(AttachmentText(name, source, status=status, notice='Supported: PDF, PNG, JPEG, text; maximum 5 MB.'))
                continue
            attachment_id = str(body.get('attachmentId', ''))
            key = attachment_id or hashlib.sha256(str(body.get('data', '')).encode()).hexdigest()
            # Gmail message parts are immutable. Cache only completed extraction,
            # keyed to the exact provider part and parser version, in case-owned data.
            previous = next((item for item in cached if item.provider_key == key
                and item.version == constants.EXTRACTION_VERSION and item.status == 'read'), None)
            if previous:
                results.append(previous)
                continue
            try:
                encoded = body.get('data')
                if attachment_id:
                    encoded = self.get('/messages/' + message_id + '/attachments/' + quote(attachment_id, safe=''), token,
                        max_bytes=constants.MAX_FILE_BYTES * 2).get('data')
                if not isinstance(encoded, str) or len(encoded) > constants.MAX_FILE_BYTES * 2:
                    raise ValueError('Invalid attachment payload')
                data = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
                result = AttachmentExtractor().extract(data, name, mime, source)
                results.append(replace(result, provider_key=key))
            except (GmailError, ValueError, httpx2.HTTPError):
                results.append(AttachmentText(name, source, notice='Attachment download failed; its contents were not read.'))
        return results

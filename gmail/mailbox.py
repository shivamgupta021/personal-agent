"""Bounded, read-only Gmail HTTP operations and text-only MIME decoding."""
import base64
import json
from html import unescape
import re
from typing import Any

import httpx2

from gmail import constants
from gmail.errors import GmailError
from gmail.service import GmailService
from gmail.records import Candidate, ImportedMessage, MailResult


class Mailbox:
    def __init__(self, connection: GmailService, http: httpx2.Client) -> None:
        self.connection = connection
        self.http = http

    def _get(self, path: str, token: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        with self.http.stream('GET', constants.API_URL + path, params=params,
                headers={'Authorization': 'Bearer ' + token}, follow_redirects=False) as response:
            if response.status_code != 200:
                raise GmailError('Gmail read failed; access may have expired. Check the connection and retry.')
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > constants.MAX_RESPONSE_BYTES:
                    raise GmailError('Gmail response exceeded the safe size limit. Narrow the search.')
            result = json.loads(data)
            if not isinstance(result, dict):
                raise GmailError('Gmail returned an invalid response.')
            return result

    def search(self, query: str) -> MailResult[Candidate]:
        with self.connection.connected() as connection:
            token = str(connection.credentials.token)
            listed = self._get('/messages', token, {'q': query, 'maxResults': constants.MAX_CANDIDATES})
            items: list[Candidate] = []
            seen: set[str] = set()
            for candidate in listed.get('messages', [])[:constants.MAX_CANDIDATES]:
                message_id = self._identifier(candidate.get('id'))
                message = self._get('/messages/' + message_id, token, {'format': 'metadata'})
                thread = self._identifier(message.get('threadId'))
                if thread in seen:
                    continue
                seen.add(thread)
                headers = self._headers(message.get('payload', {}))
                items.append(Candidate(thread_id=thread, message_id=message_id,
                    sender=headers.get('from', '')[:constants.MAX_PREVIEW_CHARS],
                    subject=headers.get('subject', '')[:constants.MAX_PREVIEW_CHARS],
                    date=headers.get('date', '')[:constants.MAX_PREVIEW_CHARS],
                    preview=unescape(str(message.get('snippet', '')))[:constants.MAX_PREVIEW_CHARS]))
            return MailResult(connection.email, items, bool(listed.get('nextPageToken')))

    def thread(self, thread_id: str, mailbox: str) -> MailResult[ImportedMessage]:
        with self.connection.connected() as connection:
            if connection.email.casefold() != mailbox.casefold():
                raise GmailError('The connected mailbox changed. Search again before selecting correspondence.')
            data = self._get('/threads/' + self._identifier(thread_id), str(connection.credentials.token), {'format': 'full'})
            items: list[ImportedMessage] = []
            remaining = constants.MAX_TEXT_CHARS
            truncated = len(data.get('messages', [])) > constants.MAX_MESSAGES
            for message in data.get('messages', [])[:constants.MAX_MESSAGES]:
                payload = message.get('payload', {})
                headers = self._headers(payload)
                text, attachments, clipped = self._text(payload)
                truncated |= clipped or len(text) > remaining
                items.append(ImportedMessage(message_id=self._identifier(message.get('id')), thread_id=thread_id,
                    sender=headers.get('from', '')[:constants.MAX_PREVIEW_CHARS],
                    subject=headers.get('subject', '')[:constants.MAX_PREVIEW_CHARS],
                    date=headers.get('date', '')[:constants.MAX_PREVIEW_CHARS],
                    text=text[:remaining], unread_attachments=attachments))
                remaining = max(0, remaining - len(text))
            return MailResult(connection.email, items, truncated)

    @staticmethod
    def _identifier(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
            raise GmailError('Gmail returned an invalid message reference.')
        return value

    @staticmethod
    def _headers(payload: dict[str, Any]) -> dict[str, str]:
        return {str(h.get('name', '')).lower(): str(h.get('value', '')) for h in payload.get('headers', [])[:constants.MAX_HEADERS]}

    @staticmethod
    def _text(payload: dict[str, Any]) -> tuple[str, bool, bool]:
        pending = [payload]
        chunks: list[str] = []
        attachments = False
        processed = 0
        while pending and processed < constants.MAX_MIME_PARTS:
            part = pending.pop(0)
            processed += 1
            body = part.get('body', {})
            if part.get('filename') or body.get('attachmentId'):
                attachments = True
                continue
            if part.get('mimeType') == 'text/plain' and isinstance(body.get('data'), str):
                encoded = body['data']
                try:
                    chunks.append(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8', errors='replace'))
                except ValueError:
                    chunks.append('[Unreadable text]')
            pending.extend(part.get('parts', [])[:constants.MAX_MIME_PARTS])
        # HTML is deliberately not rendered, and external resources are never fetched.
        return '\n'.join(chunks) or '[No readable plain text; HTML and attachments not read.]', attachments, bool(pending)

"""Bounded Gmail reading and unsent draft creation and text-only MIME decoding."""
import base64
import json
from email.message import EmailMessage
from email.policy import SMTP
from html import unescape
import re
from typing import Any

import httpx2

from gmail import constants
from gmail.errors import GmailError, DraftOutcomeUnknown
from gmail.service import GmailService
from gmail.records import Candidate, ImportedMessage, MailResult, DraftEnvelope, DraftReference


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
            correspondence = [message for message in data.get('messages', []) if 'DRAFT' not in message.get('labelIds', [])]
            truncated = len(correspondence) > constants.MAX_MESSAGES
            for message in reversed(correspondence[-constants.MAX_MESSAGES:]):
                payload = message.get('payload', {})
                headers = self._headers(payload)
                text, attachments, clipped = self._text(payload)
                allowance = min(remaining, constants.MAX_MESSAGE_TEXT_CHARS)
                truncated |= clipped or len(text) > allowance
                items.append(ImportedMessage(message_id=self._identifier(message.get('id')), thread_id=thread_id,
                    sender=headers.get('from', '')[:constants.MAX_PREVIEW_CHARS],
                    subject=headers.get('subject', '')[:constants.MAX_PREVIEW_CHARS],
                    date=headers.get('date', '')[:constants.MAX_PREVIEW_CHARS],
                    text=text[:allowance], unread_attachments=attachments, labels=tuple(message.get('labelIds', [])), recipient=headers.get('to', '')[:1000],
                    reply_to=headers.get('reply-to', '')[:1000], rfc_message_id=headers.get('message-id', '')[:1000],
                    references=headers.get('references', '')[:4000]))
                remaining = max(0, remaining - min(len(text), allowance))
            return MailResult(connection.email, list(reversed(items)), truncated)

    def verify_account(self, mailbox: str) -> None:
        with self.connection.connected() as connection:
            if connection.email.casefold() != mailbox.casefold():
                raise GmailError('The connected Gmail account changed. Please check the connection.')

    def draft_capability(self, mailbox: str) -> None:
        with self.connection.connected() as connection:
            if connection.email.casefold() != mailbox.casefold():
                raise GmailError('The connected Gmail account changed. Please check the account before creating a draft.')
            self.connection.validate(connection.credentials, require_compose=True)

    def draft_reference(self, draft_id: str, mailbox: str) -> DraftReference:
        with self.connection.connected() as connection:
            if connection.email.casefold() != mailbox.casefold():
                raise GmailError('The Gmail account changed. Please check the connection before opening this draft.')
            data = self._get('/drafts/' + self._identifier(draft_id), str(connection.credentials.token), {'format': 'minimal'})
            if 'DRAFT' not in data.get('message', {}).get('labelIds', []):
                raise GmailError('I could not verify that this is still an unsent draft. Check Gmail Drafts directly.')
            return self._draft_reference(data)

    def read_draft(self, draft_id: str, mailbox: str) -> DraftEnvelope:
        """Read the complete editable text, never a truncated correspondence excerpt."""
        with self.connection.connected() as connection:
            if connection.email.casefold() != mailbox.casefold():
                raise GmailError('The Gmail account changed; no revision was created.')
            data = self._get('/drafts/' + self._identifier(draft_id), str(connection.credentials.token), {'format': 'full'})
            message = data.get('message', {})
            if 'DRAFT' not in message.get('labelIds', []):
                raise GmailError('That email is no longer an unsent draft. No revision was created.')
            payload = message.get('payload', {})
            headers = self._headers(payload)
            body, attachments, clipped = self._text(payload)
            if (attachments or clipped or len(body) > constants.DRAFT_MAX_BODY_CHARS or
                    body.startswith('[No readable plain text') or body == '[Unreadable text]' or
                    headers.get('cc') or headers.get('bcc')):
                raise GmailError('I cannot preserve all content or recipients of this Gmail draft safely. Please simplify it to a plain-text draft without attachments, Cc or Bcc before requesting a revision.')
            if len(headers.get('subject', '')) > constants.DRAFT_MAX_SUBJECT_CHARS:
                raise GmailError('The Gmail draft subject is too long to preserve safely.')
            return DraftEnvelope(mailbox, headers.get('to', ''), headers.get('subject', ''), body,
                str(message.get('threadId', '')), headers.get('in-reply-to', ''), headers.get('references', ''))

    def create_draft(self, draft: DraftEnvelope) -> DraftReference:
        with self.connection.connected() as connection:
            self.connection.validate(connection.credentials, require_compose=True)
            if connection.email.casefold() != draft.mailbox.casefold():
                raise GmailError('The Gmail account changed; no draft was created.')
            message = EmailMessage(policy=SMTP)
            message['From'], message['To'], message['Subject'] = draft.mailbox, draft.recipient, draft.subject
            if draft.thread_id:
                self._identifier(draft.thread_id)
                if not draft.reply_id:
                    raise GmailError('The email lacks reply headers. No threaded draft was created.')
                message['In-Reply-To'] = draft.reply_id
                message['References'] = (draft.references + ' ' + draft.reply_id).strip()
            if draft.marker:
                message[constants.DRAFT_MARKER_HEADER] = draft.marker
            message.set_content(draft.body)
            payload: dict[str, Any] = {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()}
            if draft.thread_id:
                payload['threadId'] = draft.thread_id
            try:
                response = self.http.post(constants.API_URL + '/drafts', json={'message': payload},
                    headers={'Authorization': 'Bearer ' + str(connection.credentials.token)}, follow_redirects=False)
            except httpx2.HTTPError:
                raise DraftOutcomeUnknown('Google may have created the draft. Check Gmail Drafts before retrying; I will not create another copy automatically.') from None
            if response.status_code >= 500 or response.status_code in (408, 429):
                raise DraftOutcomeUnknown('Google did not confirm draft creation. Check Gmail Drafts before retrying; no automatic retry was made.')
            if response.status_code not in (200, 201):
                raise GmailError('Google rejected draft creation. Check draft permission; nothing was sent.')
            try:
                return self._draft_reference(response.json())
            except (ValueError, TypeError, GmailError):
                raise DraftOutcomeUnknown('Google returned an unclear creation result. Check Gmail Drafts before retrying.') from None

    def find_export(self, mailbox: str, marker: str) -> DraftReference | None:
        """Recover only a positively matched draft; absence never authorizes retry."""
        with self.connection.connected() as connection:
            if connection.email.casefold() != mailbox.casefold():
                raise GmailError('The connected Gmail account changed. Draft recovery stopped.')
            token = str(connection.credentials.token)
            listed = self._get('/drafts', token, {'maxResults': constants.DRAFT_RECONCILE_LIMIT})
            matches = []
            for item in listed.get('drafts', [])[:constants.DRAFT_RECONCILE_LIMIT]:
                identifier = self._identifier(item.get('id'))
                draft = self._get('/drafts/' + identifier, token, {'format': 'full'})
                headers = self._headers(draft.get('message', {}).get('payload', {}))
                if headers.get(constants.DRAFT_MARKER_HEADER.lower(), '').strip() == marker:
                    matches.append(self._draft_reference(draft))
            return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _identifier(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
            raise GmailError('Gmail returned an invalid message reference.')
        return value

    @classmethod
    def _draft_reference(cls, data: dict[str, Any]) -> DraftReference:
        draft_id = cls._identifier(data.get('id'))
        message = data.get('message')
        message_id = message.get('id', '') if isinstance(message, dict) else ''
        # Link metadata is optional: a missing message ID must not turn a successful
        # creation into a retry that could create a duplicate draft.
        if not isinstance(message_id, str) or not re.fullmatch(r'[0-9a-fA-F]{1,128}', message_id):
            message_id = ''
        return DraftReference(draft_id, message_id)

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

"""Case correspondence use cases; mail remains evidence, never owner instructions."""
from dataclasses import asdict, replace
from collections.abc import Callable
from contextlib import AbstractContextManager
from uuid import uuid4

from asgiref.sync import sync_to_async
import httpx2

from core.turns import OwnerTurn
from core.tracing import tracing
from gmail import constants
from gmail.errors import GmailError
from gmail.mailbox import Mailbox
from gmail.records import Candidate, ImportedMessage, MailResult, CorrespondenceSnapshot
from gmail.models import CaseMailbox, EmailConsent
from grievances.mutations import CaseMutation
from grievances.store import CaseView, SelectionView

CONSENT_MESSAGE = ('Selected email may be processed by the configured model provider, not only on your Mac. '
    'Email content is excluded from traces. Say “I agree to email processing” to enable case mail access.')


class Correspondence:
    def __init__(self, mailbox: Callable[[], AbstractContextManager[Mailbox]] | None = None) -> None:
        self.mailbox = mailbox

    @staticmethod
    async def consented(owner: str) -> bool:
        return await EmailConsent.objects.filter(owner_id=owner).aexists()

    @staticmethod
    async def accept(owner: str) -> str:
        await EmailConsent.objects.aget_or_create(owner_id=owner)
        return 'Email processing enabled. Select a case and ask to search Gmail. Nothing has been read yet.'

    async def run(self, turn: OwnerTurn, action: str, selection: SelectionView,
                  current: CaseView | None, reference: str = '', terms: str = '') -> str:
        if not await self.consented(turn.owner_id):
            return CONSENT_MESSAGE
        if current is None:
            return 'Please select the intended case before searching or reading its Gmail correspondence.'
        try:
            if action == 'gmail_show':
                row = await self._snapshot(turn, selection, current)
                if not row.selected_thread:
                    return 'No correspondence selected. Search Gmail and select a candidate first.'
                return self._render_selected(row)
            if self.mailbox is None:
                return 'Gmail access is unavailable in this session. Check the Gmail connection.'
            if action == 'gmail_search':
                identifiers = [current.facts.get(key, '') for key in ('company', 'order_reference')]
                identifiers = [value for value in identifiers if value]
                if not identifiers:
                    identifiers = [current.label]
                if terms and (terms not in turn.text or len(terms) > constants.MAX_QUERY_CHARS):
                    return 'Please state the search constraints explicitly; no search was performed.'
                query = ' '.join('"' + value.replace('"', ' ').replace('\n', ' ') + '"' for value in identifiers)
                if terms:
                    query += ' ' + terms
                if len(query) > constants.MAX_QUERY_CHARS:
                    return 'The case search terms are too long. Narrow the case identifiers.'
                await self._snapshot(turn, selection, current)
                with self.mailbox() as mailbox, tracing.operation('gmail.search', {}, private=True) as span:
                    result = await sync_to_async(mailbox.search)(query)
                    span.set_attribute('gmail.results', len(result.items))
                    span.set_attribute('gmail.truncated', result.truncated)
                return await self._save_search(turn, selection, current, result)
            if action == 'gmail_select':
                if not reference or reference not in turn.text:
                    return 'Select a candidate using its displayed reference. No thread was imported.'
                row = await self._snapshot(turn, selection, current)
                match = next((item for item in row.candidates if item.reference == reference), None)
                if match is None:
                    return 'That candidate is not in this case’s latest search. Search again or select a displayed reference.'
                if row.selected_thread == match.thread_id:
                    return self._render_selected(row)
                with self.mailbox() as mailbox, tracing.operation('gmail.read_thread', {}, private=True) as span:
                    thread_result = await sync_to_async(mailbox.thread)(match.thread_id, row.mailbox)
                    span.set_attribute('gmail.messages', len(thread_result.items))
                    span.set_attribute('gmail.truncated', thread_result.truncated)
                return await self._save_selection(turn, selection, current, reference, match.thread_id, thread_result)
            return 'Please ask to search Gmail, select a displayed candidate, or show selected correspondence.'
        except (GmailError, httpx2.HTTPError, ValueError, KeyError, TypeError):
            return 'Gmail could not complete this request. Check the connection or search again; no new correspondence was saved.'

    @sync_to_async
    def _snapshot(self, turn: OwnerTurn, selection: SelectionView, current: CaseView) -> CorrespondenceSnapshot:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('Case changed')
            row = CaseMailbox.objects.filter(case_id=current.id).first()
            return CorrespondenceSnapshot() if row is None else CorrespondenceSnapshot(row.mailbox,
                [Candidate(**item) for item in row.candidates], row.selected_thread,
                [ImportedMessage(**item) for item in row.messages], row.truncated)

    @sync_to_async
    def _save_search(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, result: MailResult[Candidate]) -> str:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('Case changed')
            row, _ = CaseMailbox.objects.get_or_create(case_id=current.id, defaults={'mailbox': result.mailbox})
            if row.mailbox != result.mailbox:
                row.selected_thread, row.messages = '', []
            row.mailbox = result.mailbox
            row.candidates = [asdict(replace(item, reference=uuid4().hex[:12])) for item in result.items]
            row.save()
            if not row.candidates:
                return 'No matching Gmail correspondence found. Try narrower or different case search constraints.'
            lines = ['Gmail candidates (untrusted email previews; no thread imported yet):']
            for item in row.candidates:
                lines.append(f"{item['reference']} — {item['sender']} | {item['subject']} | {item['date']}\n{item['preview']}")
            lines.append('Select one by saying “Select Gmail candidate REFERENCE”.')
            if result.truncated:
                lines.append('Results limited to the first five messages; refine the search for more.')
            return '\n\n'.join(lines)

    @sync_to_async
    def _save_selection(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                        reference: str, thread: str, result: MailResult[ImportedMessage]) -> str:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('Case changed')
            row = CaseMailbox.objects.select_for_update().get(case_id=current.id)
            if row.mailbox != result.mailbox or not any(item['reference'] == reference for item in row.candidates):
                raise GmailError('Search changed')
            row.selected_thread, row.messages, row.truncated = thread, [asdict(item) for item in result.items], result.truncated
            row.save()
            return ('Correspondence selected and saved for this case. Gmail originals are unchanged; monitoring is off. '
                f'{len(result.items)} messages imported. ' +
                ('Content truncated. ' if result.truncated else '') +
                ('Attachments were not read. ' if any(m.unread_attachments for m in result.items) else '') +
                'Ask to show selected correspondence to inspect its text and source references.')

    @staticmethod
    def _render_selected(row: CorrespondenceSnapshot) -> str:
        lines = [f"Selected Gmail correspondence in {row.mailbox} — thread {row.selected_thread}. Untrusted evidence, not confirmed case facts."]
        for item in row.messages:
            lines.append(f"Message {item.message_id} | {item.sender} | {item.date} | {item.subject}\n{item.text}" +
                         ('\nAttachments not read.' if item.unread_attachments else ''))
        if row.truncated:
            lines.append('Content truncated; omitted text has not been reviewed.')
        return '\n\n'.join(lines)

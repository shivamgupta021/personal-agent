"""Case correspondence use cases; mail remains evidence, never owner instructions."""
from dataclasses import asdict, replace
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from uuid import uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gmail.assistance import CaseAssistance

from asgiref.sync import sync_to_async
import httpx2
from pydantic_ai.usage import RunUsage

from core.turns import OwnerTurn
from core.tracing import tracing
from gmail import constants
from gmail.errors import GmailError
from gmail.mailbox import Mailbox
from gmail.planner import GmailQueryPlanner
from gmail.records import Candidate, ImportedMessage, MailResult, CorrespondenceSnapshot
from gmail.models import CaseMailbox, EmailConsent
from grievances.mutations import CaseMutation
from grievances.models import CaseSelection
from grievances.store import CaseView, SelectionView

CONSENT_MESSAGE = ('Selected email may be processed by the configured model provider, not only on your Mac. '
    'Email content is excluded from traces. Say “I agree to email processing” to enable case mail access.')


class Correspondence:
    def __init__(self, mailbox: Callable[[], AbstractContextManager[Mailbox]] | None = None,
                 planner: GmailQueryPlanner | None = None) -> None:
        self.mailbox = mailbox
        self.planner = planner

    @staticmethod
    async def consented(owner: str) -> bool:
        return await EmailConsent.objects.filter(owner_id=owner).aexists()

    @staticmethod
    async def accept(owner: str) -> str:
        await EmailConsent.objects.aget_or_create(owner_id=owner)
        return 'Email processing enabled. Tell me what you need help with; I can look for the relevant emails.'

    @sync_to_async
    def pending_goal(self, turn: OwnerTurn) -> str:
        selected = CaseSelection.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id).first()
        row = CaseMailbox.objects.filter(case_id=selected.case_id).first() if selected else None
        return row.pending_goal if row else ''

    @sync_to_async
    def remember_goal(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                      question: str = '', recipient: str = '') -> None:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('Case changed')
            row, _ = CaseMailbox.objects.get_or_create(case_id=current.id, defaults={'mailbox': ''})
            if not row.pending_goal:
                row.pending_goal = turn.text
            row.pending_question = question
            if recipient and recipient in turn.text:
                row.pending_recipient = recipient
            row.save()

    async def run(self, turn: OwnerTurn, action: str, selection: SelectionView,
                  current: CaseView | None, reference: str = '',
                  usage: RunUsage | None = None, request_limit: int | None = None) -> str:
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
                if self.planner is None:
                    return 'Gmail query planning is unavailable in this session.'
                result = await self._search(turn, selection, current, usage, request_limit)
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

    async def _search(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                      usage: RunUsage | None, request_limit: int | None) -> MailResult[Candidate]:
        if self.planner is None or self.mailbox is None:
            raise GmailError('Gmail search is unavailable')
        identifiers = {key: current.facts[key] for key in ('company', 'order_reference') if current.facts.get(key)}
        await self._snapshot(turn, selection, current)
        with tracing.operation('gmail.plan_queries', {}, kind='agent', private=True) as span:
            plan = await self.planner.run(turn.text, current.label, identifiers, usage, request_limit)
            span.set_attribute('gmail.query_count', len(plan.queries))
        items: list[Candidate] = []
        seen: set[str] = set()
        account = ''
        truncated = False
        with self.mailbox() as mailbox:
            for query in plan.queries:
                await self._snapshot(turn, selection, current)
                with tracing.operation('gmail.search', {}, private=True) as span:
                    found = await sync_to_async(mailbox.search)(query)
                    span.set_attribute('gmail.results', len(found.items))
                    span.set_attribute('gmail.truncated', found.truncated)
                if account and account != found.mailbox:
                    raise GmailError('Mailbox changed during search')
                account = found.mailbox
                truncated |= found.truncated
                for candidate in found.items:
                    if candidate.thread_id not in seen:
                        seen.add(candidate.thread_id)
                        items.append(candidate)
                if items:
                    break  # Broader alternatives are only for an empty preceding search.
        result = MailResult(account, items[:constants.MAX_CANDIDATES], truncated)
        return result

    async def assist(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                     usage: RunUsage, assistant: 'CaseAssistance', *, draft: bool = False, recipient: str = '') -> str:
        try:
            row = await self._snapshot(turn, selection, current)
            goal = row.pending_goal or turn.text
            request = json.dumps({'original_goal': goal, 'owner_reply': turn.text})
            if row.selected_thread:
                if self.mailbox is None:
                    raise GmailError('Gmail is unavailable')
                with self.mailbox() as mailbox, tracing.operation('gmail.refresh_thread', {}, private=True) as span:
                    fresh = await sync_to_async(mailbox.thread)(row.selected_thread, row.mailbox)
                    span.set_attribute('gmail.messages', len(fresh.items))
                    span.set_attribute('gmail.truncated', fresh.truncated)
                await self._save_refresh(turn, selection, current, row.selected_thread, fresh)
                row = await self._snapshot(turn, selection, current)
            else:
                if not row.pending_question or not row.candidates:
                    found = await self._search(replace(turn, text=request), selection, current, usage, constants.ASSISTANCE_REQUEST_LIMIT)
                    await self._save_search(turn, selection, current, found)
                    row = await self._snapshot(turn, selection, current)
                if not row.candidates and draft and recipient:
                    return await assistant.draft(turn, selection, current, replace(row, pending_goal=goal), usage, recipient)
                if not row.candidates:
                    question = 'I could not find a matching email. Do you know the sender or roughly when it arrived?'
                    await self.remember_goal(turn, selection, current, question)
                    return question
                match_request = json.dumps({'original_goal': row.pending_goal, 'previous_question': row.pending_question, 'owner_reply': turn.text})
                match = await assistant.match(match_request, current, [asdict(item) for item in row.candidates], usage)
                candidate = next((item for item in row.candidates if item.reference == match.reference), None)
                if candidate is None:
                    question = match.question or 'I found emails, but cannot reliably identify the right conversation. Which booking or application is this about?'
                    await self.remember_goal(turn, selection, current, question)
                    return question
                if self.mailbox is None:
                    raise GmailError('Gmail is unavailable')
                await self._snapshot(turn, selection, current)
                with self.mailbox() as mailbox, tracing.operation('gmail.read_thread', {}, private=True) as span:
                    result = await sync_to_async(mailbox.thread)(candidate.thread_id, row.mailbox)
                    span.set_attribute('gmail.messages', len(result.items))
                    span.set_attribute('gmail.truncated', result.truncated)
                await self._save_selection(turn, selection, current, candidate.reference, candidate.thread_id, result)
                row = await self._snapshot(turn, selection, current)
            if draft:
                return await assistant.draft(turn, selection, current, replace(row, pending_goal=goal), usage, recipient)
            answer = await assistant.explain(request, current,
                [asdict(message) for message in row.messages], row.truncated, usage)
            await self._snapshot(turn, selection, current)
            return answer
        except (GmailError, httpx2.HTTPError, ValueError, KeyError, TypeError):
            return 'I could not read the relevant emails just now. Please try again; I have not sent anything.'

    @sync_to_async
    def _save_refresh(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                      thread: str, result: MailResult[ImportedMessage]) -> None:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('Case changed')
            row = CaseMailbox.objects.select_for_update().get(case_id=current.id)
            if row.mailbox != result.mailbox or row.selected_thread != thread:
                raise GmailError('Correspondence changed')
            row.messages = [asdict(item) for item in result.items]
            row.truncated = result.truncated
            row.save()

    @sync_to_async
    def _snapshot(self, turn: OwnerTurn, selection: SelectionView, current: CaseView) -> CorrespondenceSnapshot:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('Case changed')
            row = CaseMailbox.objects.filter(case_id=current.id).first()
            return CorrespondenceSnapshot() if row is None else CorrespondenceSnapshot(row.mailbox,
                [Candidate(**item) for item in row.candidates], row.selected_thread,
                [ImportedMessage(**item) for item in row.messages], row.truncated, row.pending_goal, row.pending_question, row.pending_recipient)

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
                lines.append('Search reached its bounded result limit; refine the search for more.')
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
            row.pending_goal, row.pending_question = '', ''
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

"""Explicit, read-only case reply checks with durable provenance and deduplication."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from email.utils import getaddresses
import hashlib
import json
from typing import Literal

from asgiref.sync import sync_to_async
import httpx2
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.tracing import tracing
from core.workers import drain_on_cancel
from core.turns import OwnerTurn
from gmail import constants
from gmail.errors import GmailError, GmailBusy
from gmail.mailbox import Mailbox
from gmail.models import CaseMailbox, EmailConsent, GmailDraftExport, GmailReplyCheckState
from gmail.records import ImportedMessage, MailResult
from gmail.reply_constants import MAX_REPLY_FINDINGS, MAX_REPLY_QUOTE_CHARS, MAX_REPLY_SUMMARY_CHARS, MAX_SEEN_MESSAGES
from gmail.reply_prompts import REPLY_CHECK_INSTRUCTIONS
from grievances.mutations import CaseMutation
from grievances.progress_store import ProgressStore
from grievances.store import CaseView, SelectionView


class ReplyFinding(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)
    classification: Literal['substantive', 'acknowledgement', 'uncertain']
    summary: str = Field(min_length=1, max_length=MAX_REPLY_SUMMARY_CHARS)
    quote: str = Field(min_length=1, max_length=MAX_REPLY_QUOTE_CHARS)


class ReplyAnalysis(BaseModel):
    findings: list[ReplyFinding] = Field(max_length=MAX_REPLY_FINDINGS)


@dataclass(frozen=True)
class ReplySnapshot:
    mailbox: str
    thread_id: str
    seen: tuple[str, ...]
    fingerprint: str
    sent_matches: tuple[dict[str, str | int], ...] = ()
    findings: tuple[ReplyFinding, ...] = ()
    analysis_fingerprint: str = ''
    messages: tuple[ImportedMessage, ...] = ()


@dataclass(frozen=True)
class ReplyCheckResult:
    answer: str
    verified: bool = False
    findings: tuple[ReplyFinding, ...] = ()
    message_ids: tuple[str, ...] = ()
    truncated: bool = False
    unread_attachments: bool = False


class GmailReplyChecks:
    def __init__(self, model: Model, mailbox: Callable[[], AbstractContextManager[Mailbox]]) -> None:
        self.mailbox = mailbox
        self.agent: Agent[list[ImportedMessage], ReplyAnalysis] = Agent(model, deps_type=list[ImportedMessage], output_type=ReplyAnalysis, name='gmail_reply_check',
            instructions=REPLY_CHECK_INSTRUCTIONS, retries=constants.PLANNER_RETRIES,
            model_settings={'max_tokens': constants.DRAFT_MAX_OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()
        self.agent.output_validator(self.validate_analysis)

    @staticmethod
    def validate_analysis(ctx: RunContext[list[ImportedMessage]], analysis: ReplyAnalysis) -> ReplyAnalysis:
        try:
            GmailReplyChecks.validate_findings(ctx.deps, analysis.findings)
        except GmailError:
            if ctx.retry < constants.PLANNER_RETRIES:
                raise ModelRetry('Each received message needs one finding. Copy its quote only from that exact message_id, not another email or its quoted history. For truncated evidence, use a visible excerpt or subject and mark uncertainty.') from None
        return analysis

    async def run(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                  usage: RunUsage | None = None) -> str:
        return (await self.check(turn, selection, current, usage)).answer

    async def check(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                    usage: RunUsage | None = None, *, reuse_analysis: bool = True) -> ReplyCheckResult:
        if not await EmailConsent.objects.filter(owner_id=turn.owner_id).aexists():
            return ReplyCheckResult('Please enable email processing before I inspect your correspondence.')
        try:
            snapshot = await self.snapshot(turn, selection, current)
            with self.mailbox() as mailbox, tracing.operation('gmail.check_replies', {'thread_id': snapshot.thread_id}, private=True) as span:
                try:
                    result = await drain_on_cancel(sync_to_async(mailbox.thread)(snapshot.thread_id, snapshot.mailbox, list(snapshot.messages)))
                except GmailBusy:
                    tracing.record_output(span, {'status': 'deferred', 'reason': 'Gmail connection busy'})
                    raise
                tracing.record_output(span, asdict(result))
                span.set_attribute('gmail.messages', len(result.items))
                span.set_attribute('gmail.truncated', result.truncated)
            if not result.items:
                raise GmailError('Selected thread returned no messages')
            if result.mailbox.casefold() != snapshot.mailbox.casefold() or any(
                    message.thread_id != snapshot.thread_id for message in result.items):
                raise GmailError('Correspondence account or thread changed')
            # Keep a single copy of each provider message; drafts never count as received or sent mail.
            messages = list({message.message_id: message for message in result.items}.values())
            incoming = [message for message in messages if 'DRAFT' not in message.labels
                and self.addresses(message.sender) != [snapshot.mailbox.casefold()]]
            findings: list[ReplyFinding] = []
            if reuse_analysis and incoming and self.analysis_key(incoming, result.truncated) == snapshot.analysis_fingerprint:
                findings = list(snapshot.findings)
                self.validate_findings(incoming, findings)
            elif incoming:
                analyzed = await self.agent.run(json.dumps({'received_messages': [message.evidence() for message in incoming], 'truncated': result.truncated}),
                    deps=incoming,
                    usage=usage, usage_limits=UsageLimits(request_limit=constants.ASSISTANCE_REQUEST_LIMIT),
                    timeout=constants.ASSISTANCE_TIMEOUT_SECONDS)
                findings = analyzed.output.findings
                self.validate_findings(incoming, findings)
            sent_matches, ambiguous = await self.sent_matches(turn.owner_id, current.id, snapshot.mailbox, messages, result.truncated)
            sent_matches = list({(item['message_id'], item['draft_version']): item
                for item in (*snapshot.sent_matches, *sent_matches)}.values())
            progress = await ProgressStore().snapshot(turn.owner_id, current.id)
            owner_versions = [report.draft_version for report in progress.sent] if progress else []
            answer = self.render(messages, incoming, findings, snapshot, result.truncated,
                                 sent_matches, ambiguous, owner_versions)
            with self.mailbox() as mailbox:
                await sync_to_async(mailbox.verify_account)(snapshot.mailbox)
            await self.save(turn, selection, current, snapshot, result, findings, sent_matches, answer)
            return ReplyCheckResult(answer, True, tuple(findings),
                tuple(message.message_id for message in messages if 'DRAFT' not in message.labels), result.truncated, any(message.unread_attachments for message in messages))
        except GmailBusy:
            raise
        except (GmailError, httpx2.HTTPError, ValueError, KeyError, TypeError):
            return ReplyCheckResult('I could not verify the reply status just now. The previous record is unchanged; please try again.')

    @staticmethod
    def analysis_key(messages: list[ImportedMessage], truncated: bool) -> str:
        payload = {'messages': [asdict(message) for message in messages], 'truncated': truncated,
                   'instructions': REPLY_CHECK_INSTRUCTIONS}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def addresses(value: str) -> list[str]:
        return [address.casefold() for _, address in getaddresses([value]) if address]

    @staticmethod
    def fingerprint(row: CaseMailbox) -> str:
        return hashlib.sha256(json.dumps({'mailbox': row.mailbox, 'thread': row.selected_thread,
            'messages': row.messages}, sort_keys=True).encode()).hexdigest()

    @sync_to_async
    def snapshot(self, turn: OwnerTurn, selection: SelectionView, current: CaseView) -> ReplySnapshot:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('Case changed')
            row = CaseMailbox.objects.filter(case_id=current.id).first()
            if row is None or not row.selected_thread:
                raise GmailError('No selected correspondence')
            checked = GmailReplyCheckState.objects.filter(mailbox_record=row).first()
            if checked and checked.mailbox == row.mailbox and checked.thread_id == row.selected_thread:
                seen = tuple(checked.seen_message_ids)
                matches = tuple(checked.sent_matches)
                findings = tuple(ReplyFinding.model_validate(item) for item in checked.findings)
            else:
                seen = tuple(item['message_id'] for item in row.messages)
                matches = ()
                findings = ()
            return ReplySnapshot(row.mailbox, row.selected_thread, seen, self.fingerprint(row), matches, findings, checked.analysis_fingerprint if checked else '', tuple(ImportedMessage(**item) for item in row.messages))

    @staticmethod
    def validate_findings(messages: list[ImportedMessage], findings: list[ReplyFinding]) -> None:
        sources = {message.message_id: message.subject + '\n' + message.text for message in messages}
        if len(findings) != len(sources) or {finding.message_id for finding in findings} != set(sources):
            raise GmailError('Incomplete reply classification')
        if any(not finding.quote.strip() or ' '.join(finding.quote.split()) not in
               ' '.join(sources[finding.message_id].split()) for finding in findings):
            raise GmailError('Unsupported reply evidence')

    @sync_to_async
    def sent_matches(self, owner_id: str, case_id: str, mailbox: str,
                     messages: list[ImportedMessage], truncated: bool) -> tuple[list[dict[str, str | int]], bool]:
        if truncated:
            return [], False
        exports = list(GmailDraftExport.objects.filter(draft__case_id=case_id,
            draft__case__owner_id=owner_id, mailbox=mailbox, status='created').select_related('draft'))
        matches: list[dict[str, str | int]] = []
        ambiguous = False
        for message in messages:
            if 'SENT' not in message.labels or 'DRAFT' in message.labels or self.addresses(message.sender) != [mailbox.casefold()]:
                continue
            candidates = [export for export in exports if export.thread_id == message.thread_id
                and len(self.addresses(export.recipient)) == 1
                and self.addresses(message.recipient) == self.addresses(export.recipient)
                and ' '.join(message.text.split()) == ' '.join(export.draft.body.split())]
            if len(candidates) == 1:
                matches.append({'provenance': 'gmail_verified', 'message_id': message.message_id,
                    'draft_version': candidates[0].draft.version})
            elif len(candidates) > 1:
                ambiguous = True
        return matches, ambiguous

    @staticmethod
    def render(messages: list[ImportedMessage], incoming: list[ImportedMessage], findings: list[ReplyFinding],
               snapshot: ReplySnapshot, truncated: bool, matches: list[dict[str, str | int]],
               ambiguous: bool, owner_versions: list[int]) -> str:
        new_count = sum(message.message_id not in snapshot.seen for message in incoming)
        if incoming:
            by_id = {finding.message_id: finding for finding in findings}
            fresh = [by_id[message.message_id] for message in incoming if message.message_id not in snapshot.seen]
            shown = fresh or [by_id[incoming[-1].message_id]]
            prefix = f'{new_count} new received message(s) since the previous review.' if fresh else 'No newly received messages in the retrieved thread. Latest received message:'
            labels = {'substantive': 'Substantive response', 'acknowledgement': 'Acknowledgement', 'uncertain': 'Uncertain response'}
            answer = prefix + '\n' + '\n'.join(f'- {labels[finding.classification]}: {finding.summary}' for finding in shown)
            if any(finding.classification == 'acknowledgement' for finding in shown):
                answer += '\nAcknowledgements acknowledge receipt, rather than answer the issue.'
            if any(finding.classification == 'uncertain' for finding in shown):
                answer += '\nI cannot reliably tell whether the uncertain response answers the issue. Do you consider it a substantive reply?'
        else:
            answer = 'No received reply appears in the retrieved thread.'
        if matches:
            versions = ', '.join(str(item['draft_version']) for item in matches)
            answer += f'\nGmail verifies sending of draft version(s) {versions}.'
        if ambiguous:
            answer += '\nA sent email matches multiple saved draft versions. Which version did you send?'
        elif owner_versions and not matches:
            answer += '\nSending is owner-reported, not verified against a matching Gmail sent message.'
        elif not matches:
            answer += '\nI have not verified that an application draft was sent.'
        if truncated:
            answer += '\nOnly part of the thread was retrieved; this cannot establish the complete reply status.'
        if any(message.unread_attachments for message in messages):
            answer += '\nAttachments were not read.'
        return answer

    @sync_to_async
    def save(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, snapshot: ReplySnapshot,
             result: MailResult[ImportedMessage], findings: list[ReplyFinding],
             matches: list[dict[str, str | int]], answer: str) -> None:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('Case changed')
            row = CaseMailbox.objects.select_for_update().filter(case_id=current.id).first()
            if row is None or self.fingerprint(row) != snapshot.fingerprint:
                raise GmailError('Correspondence changed')
            state, _ = GmailReplyCheckState.objects.get_or_create(mailbox_record=row)
            state.mailbox, state.thread_id = snapshot.mailbox, snapshot.thread_id
            correspondence = [message for message in result.items if 'DRAFT' not in message.labels]
            state.seen_message_ids = list(dict.fromkeys([*snapshot.seen,
                *(message.message_id for message in correspondence)]))[-MAX_SEEN_MESSAGES:]
            incoming = [message for message in correspondence if self.addresses(message.sender) != [snapshot.mailbox.casefold()]]
            state.analysis_fingerprint = self.analysis_key(incoming, result.truncated)
            state.findings = [finding.model_dump() for finding in findings]
            state.sent_matches, state.last_summary = matches, answer
            state.save()
            row.messages = list({message.message_id: asdict(message) for message in correspondence}.values())
            row.truncated = result.truncated
            row.save()

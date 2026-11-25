"""Generate grounded, unsent Gmail drafts with durable export intent and replay."""
from dataclasses import asdict, replace
from email.utils import getaddresses
import hashlib
import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from urllib.parse import quote

from asgiref.sync import sync_to_async
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.tracing import tracing
from core.turns import OwnerTurn
from gmail import constants
from gmail.errors import GmailError, DraftOutcomeUnknown, GmailComposeRequired
from gmail.mailbox import Mailbox
from gmail.models import CaseMailbox, GmailDraftExport
from gmail.records import CorrespondenceSnapshot, DraftEnvelope
from gmail.prompts import GMAIL_DRAFT_INSTRUCTIONS
from gmail.revisions import DraftRevisions
from grievances.models import ComplaintDraft, CaseResearch, CaseResearchBrief, CaseMessage
from attachments.constants import MAX_FILES
from grievances.mutations import CaseMutation
from grievances.store import CaseView, SelectionView


class EmailQuote(BaseModel):
    message_id: str
    quote: str = Field(min_length=1, max_length=constants.DRAFT_MAX_QUOTE_CHARS)


class EmailDraft(BaseModel):
    subject: str = Field(min_length=1, max_length=constants.DRAFT_MAX_SUBJECT_CHARS)
    body: str = Field(min_length=1, max_length=constants.DRAFT_MAX_BODY_CHARS)
    sources: list[EmailQuote] = Field(default_factory=list, max_length=constants.DRAFT_MAX_SOURCES)
    question: str = Field(default='', max_length=constants.DRAFT_MAX_QUESTION_CHARS)


class GmailDraftService:
    def __init__(self, model: Model, mailbox: Callable[[], AbstractContextManager[Mailbox]]) -> None:
        self.mailbox = mailbox
        self.writer = Agent(model, name='gmail_draft_writer', output_type=EmailDraft,
            instructions=GMAIL_DRAFT_INSTRUCTIONS, retries=constants.PLANNER_RETRIES,
            model_settings={'max_tokens': constants.DRAFT_MAX_OUTPUT_TOKENS})
        self.writer.instrument = tracing.private_instrumentation()

    @staticmethod
    def fingerprint(value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def addresses(value: str) -> list[str]:
        return list(dict.fromkeys(address.casefold() for _, address in getaddresses([value])
            if re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', address)))

    async def open(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, version: int | None) -> str:
        rows = GmailDraftExport.objects.filter(draft__case_id=current.id,
            draft__case__owner_id=turn.owner_id, status='created').select_related('draft')
        if version is not None:
            rows = rows.filter(draft__version=version)
        choices = [row async for row in rows.order_by('-draft__version')[:2]]
        if not choices:
            return 'I could not find a saved Gmail draft for this case.'
        if len(choices) > 1:
            return f'Which draft version should I open? The latest is v{choices[0].draft.version}.'
        row = choices[0]
        try:
            with self.mailbox() as mailbox, tracing.operation('gmail.open_draft', {'draft_id': row.gmail_id}, private=True) as span:
                reference = await sync_to_async(mailbox.draft_reference)(row.gmail_id, row.mailbox)
                tracing.record_output(span, asdict(reference))
            return await self.save_link(turn, selection, current, row.pk, reference.message_id)
        except GmailError:
            return 'I could not verify this draft in Gmail. It may have been changed, sent or deleted; I have not inferred which. Check Gmail Drafts directly.'

    @staticmethod
    @sync_to_async
    def save_link(turn: OwnerTurn, selection: SelectionView, current: CaseView, identifier: int, message_id: str) -> str:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                return 'The case changed while I was opening the draft. Please try again.'
            row = GmailDraftExport.objects.filter(pk=identifier, draft__case_id=current.id).select_related('draft').first()
            if row is None:
                return 'The local case was deleted. Any Gmail copy is preserved.'
            row.message_id = message_id
            row.save(update_fields=['message_id'])
            return GmailDraftService.describe(row)

    async def run(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                  row: CorrespondenceSnapshot, usage: RunUsage, recipient: str = '',
                  revision: bool = False, version: int | None = None) -> str:
        prior = await self.replay(turn)
        if prior:
            return prior
        if recipient and recipient not in turn.text:
            return 'Which email address should I address the draft to?'
        recipient = recipient or row.pending_recipient
        latest = row.messages[-1] if row.messages else None
        if recipient:
            choices = self.addresses(recipient)
        else:
            incoming = [message for message in row.messages if self.addresses(message.sender) != [row.mailbox.casefold()]]
            incoming_source = incoming[-1] if incoming else latest
            choices = self.addresses((incoming_source.reply_to or incoming_source.sender) if incoming and incoming_source else incoming_source.recipient if incoming_source else '')
        choices = [address for address in choices if address != row.mailbox.casefold()]
        if len(choices) != 1:
            return 'I cannot determine one reply address reliably. Which email address should receive the draft?'
        target = choices[0]
        if row.selected_thread and (latest is None or not latest.rfc_message_id):
            return 'I could not retrieve the reply headers needed to keep this draft in the email conversation. Please ask me to check the emails again.'
        export_id: int | None = None
        ready = False
        try:
            with self.mailbox() as mailbox:
                await sync_to_async(mailbox.draft_capability)(row.mailbox)
            revisions = DraftRevisions(self.mailbox)
            source = await revisions.read(turn, selection, current, row.mailbox, row.selected_thread, version) if revision else None
            if source and recipient and DraftRevisions.address(recipient) != DraftRevisions.address(source.envelope.recipient):
                return 'This revision would change the original recipient. Please request a separate draft for the new recipient.'
            policy = await self.policy_snapshot(turn, current)
            previous = await ComplaintDraft.objects.filter(case_id=current.id, case__owner_id=turn.owner_id).order_by('-version').afirst()
            uploads = await sync_to_async(self.upload_sources)(current.id)
            result = await self.writer.run(json.dumps({'request': {'original_goal': row.pending_goal or turn.text, 'owner_reply': turn.text}, 'facts': current.facts,
                'uploaded_evidence': uploads, 'reviewed_policy': policy, 'emails': [item.evidence() for item in row.messages], 'truncated': row.truncated,
                'selected_gmail_draft': {'version': source.version, 'subject': source.envelope.subject,
                    'body': source.envelope.body, 'threaded': bool(source.envelope.thread_id), 'provenance': 'Current unsent Gmail draft, including manual edits; not sent correspondence or factual evidence.'} if source else None,
                'previous_application_draft': {'version': previous.version, 'subject': previous.subject, 'body': previous.body} if previous and not source else None}), usage=usage,
                usage_limits=UsageLimits(request_limit=constants.ASSISTANCE_REQUEST_LIMIT),
                timeout=constants.ASSISTANCE_TIMEOUT_SECONDS)
            content = result.output
            if content.question:
                return content.question
            sources = {message.message_id: message.subject + '\n' + message.text for message in row.messages}
            sources.update(uploads)
            if sources and (not content.sources or any(quote.message_id not in sources or
                    quote.quote not in sources[quote.message_id] for quote in content.sources)):
                return 'I could not verify the draft against the emails. No Gmail draft was created.'
            envelope = DraftEnvelope(row.mailbox, target, latest.subject if latest else content.subject,
                content.body, row.selected_thread, latest.rfc_message_id if latest else '', latest.references if latest else '')
            if source:
                if source.envelope.thread_id and content.subject != source.envelope.subject:
                    return 'Changing the subject would break the existing Gmail conversation. No revision was created; request a separate email if you want a different subject.'
                envelope = replace(source.envelope, subject=content.subject, body=content.body, marker='')
                await revisions.validate(source)
            evidence = self.fingerprint([[asdict(item) for item in row.messages], uploads])
            export_id, ready = await self.reserve(turn, selection, current, envelope, evidence, policy)
            if not ready:
                await self.reconcile(export_id)
                if await GmailDraftExport.objects.filter(pk=export_id, status='created').aexists():
                    await self.clear_pending(turn, selection, current)
                return await self.result(export_id)
            envelope = replace(envelope, marker=await self.marker(export_id))
            # Recheck deletion, facts, selection and evidence immediately before the external handoff.
            await self.validate_handoff(turn, selection, current, row.selected_thread, evidence)
            if policy != await self.policy_snapshot(turn, current):
                raise GmailError('The policy evidence changed; no draft was created.')
            if source:
                await revisions.validate(source)
                await self.validate_handoff(turn, selection, current, row.selected_thread, evidence)
            with self.mailbox() as mailbox, tracing.operation('gmail.create_draft', asdict(envelope), private=True) as span:
                reference = await sync_to_async(mailbox.create_draft)(envelope)
                tracing.record_output(span, asdict(reference))
            await self.finish(export_id, 'created', reference.draft_id, reference.message_id)
            await self.clear_pending(turn, selection, current)
            return await self.result(export_id)
        except GmailComposeRequired:
            raise
        except DraftOutcomeUnknown as error:
            if export_id is not None:
                await self.finish(export_id, 'uncertain')
            return str(error)
        except GmailError as error:
            if export_id is not None and ready:
                await self.finish(export_id, 'failed')
            return str(error)

    @staticmethod
    @sync_to_async
    def replay(turn: OwnerTurn) -> str | None:
        row = GmailDraftExport.objects.filter(draft__operation__owner_id=turn.owner_id,
            draft__operation__conversation_id=turn.conversation_id, draft__operation__delivery_id=turn.delivery_id).select_related('draft').first()
        return GmailDraftService.describe(row) if row else None

    @staticmethod
    def describe(row: GmailDraftExport) -> str:
        if row.status == 'created':
            link = f'https://mail.google.com/mail/?authuser={quote(row.mailbox, safe="")}#inbox?compose={row.message_id}' if row.message_id else ''
            return (f'Draft v{row.draft.version} is ready for your review in Gmail.\nTo: {row.recipient}\n\n'
                + (f'[Open in Gmail]({link})' if link else
                   f'Find “{row.draft.subject}” in Gmail Drafts for {row.mailbox}.'))
        if row.status == 'failed':
            return 'Gmail did not create this draft. Check the connection or permission before requesting it again.'
        return 'Draft creation is unconfirmed. Check Gmail Drafts before retrying; I will not create another copy automatically.'

    @sync_to_async
    def reserve(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                envelope: DraftEnvelope, evidence: str, policy: dict[str, object]) -> tuple[int, bool]:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure or mutation.case is None:
                raise GmailError('The case changed; no draft was created.')
            existing = GmailDraftExport.objects.filter(draft__operation__owner_id=turn.owner_id,
                draft__operation__conversation_id=turn.conversation_id, draft__operation__delivery_id=turn.delivery_id).first()
            if existing:
                return existing.pk, False
            fingerprint = self.fingerprint(asdict(envelope))
            uncertain = GmailDraftExport.objects.filter(draft__case_id=current.id, mailbox=envelope.mailbox,
                status__in=['pending', 'uncertain']).first()
            if uncertain:
                return uncertain.pk, False
            self.check_evidence(current, envelope.thread_id, evidence)
            latest = ComplaintDraft.objects.filter(case_id=current.id).order_by('-version').first()
            receipt = mutation.record('gmail_draft', mutation.case)
            draft = ComplaintDraft.objects.create(case=mutation.case, version=latest.version + 1 if latest else 1,
                fact_revision=current.revision, operation=receipt, subject=envelope.subject, body=envelope.body,
                kind='follow_up' if envelope.thread_id else 'initial', progress_revision=current.progress_revision,
                evidence_revision=policy.get('revision'))
            export = GmailDraftExport.objects.create(draft=draft, mailbox=envelope.mailbox,
                recipient=DraftRevisions.address(envelope.recipient),
                thread_id=envelope.thread_id, fingerprint=fingerprint)
            return export.pk, True

    @staticmethod
    def upload_sources(case_id: str) -> dict[str, str]:
        return {f'upload:{row.pk}': row.text for row in CaseMessage.objects.filter(
            case_id=case_id, role='attachment').order_by('-pk')[:MAX_FILES]}

    @classmethod
    def check_evidence(cls, current: CaseView, thread: str, evidence: str) -> None:
        mailbox = CaseMailbox.objects.filter(case_id=current.id).first()
        if mailbox is None or mailbox.selected_thread != thread or cls.fingerprint([mailbox.messages, cls.upload_sources(current.id)]) != evidence:
            raise GmailError('The correspondence changed; no draft was created.')

    @sync_to_async
    def validate_handoff(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                         thread: str, evidence: str) -> None:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('The case changed; no draft was created.')
            self.check_evidence(current, thread, evidence)

    @staticmethod
    @sync_to_async
    def finish(identifier: int, status: str, gmail_id: str = '', message_id: str = '') -> None:
        GmailDraftExport.objects.filter(pk=identifier).update(status=status, gmail_id=gmail_id, message_id=message_id)

    @staticmethod
    @sync_to_async
    def result(identifier: int) -> str:
        row = GmailDraftExport.objects.filter(pk=identifier).select_related('draft').first()
        return GmailDraftService.describe(row) if row else 'The local case was deleted. Any Gmail copy is preserved.'

    @staticmethod
    @sync_to_async
    def clear_pending(turn: OwnerTurn, selection: SelectionView, current: CaseView) -> None:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if not mutation.failure:
                CaseMailbox.objects.filter(case_id=current.id).update(pending_goal='', pending_question='', pending_recipient='')

    @staticmethod
    @sync_to_async
    def marker(identifier: int) -> str:
        row = GmailDraftExport.objects.get(pk=identifier)
        return f'{row.pk}-{row.fingerprint}'

    async def reconcile(self, identifier: int) -> None:
        row = await GmailDraftExport.objects.filter(pk=identifier, status__in=['pending', 'uncertain']).afirst()
        if row is None:
            return
        with self.mailbox() as mailbox, tracing.operation('gmail.reconcile_draft', {'export_id': identifier}, private=True) as span:
            found = await sync_to_async(mailbox.find_export)(row.mailbox, await self.marker(identifier))
            tracing.record_output(span, {'match': asdict(found) if found else None})
        if found:
            await self.finish(identifier, 'created', found.draft_id, found.message_id)

    @staticmethod
    @sync_to_async
    def policy_snapshot(turn: OwnerTurn, current: CaseView) -> dict[str, object]:
        brief = CaseResearchBrief.objects.filter(case_id=current.id, case__owner_id=turn.owner_id).order_by('-revision').first()
        evidence = CaseResearch.objects.filter(case_id=current.id, case__owner_id=turn.owner_id).order_by('-revision').first()
        if (brief is None or evidence is None or not brief.confirmed or
                brief.fact_revision != current.revision or evidence.fact_revision != current.revision or
                evidence.brief_revision != brief.revision or evidence.status != 'verified_sources'):
            return {}
        return {'revision': evidence.revision, 'sources': evidence.sources,
                'findings': evidence.findings, 'uncertainty': evidence.uncertainty}

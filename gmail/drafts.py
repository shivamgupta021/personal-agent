"""Generate grounded, unsent Gmail drafts with durable export intent and replay."""
from dataclasses import asdict, replace
from email.utils import getaddresses
import hashlib
import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager

from asgiref.sync import sync_to_async
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.tracing import tracing
from core.turns import OwnerTurn
from gmail import constants
from gmail.errors import GmailError, DraftOutcomeUnknown
from gmail.mailbox import Mailbox
from gmail.models import CaseMailbox, GmailDraftExport
from gmail.records import CorrespondenceSnapshot, DraftEnvelope
from gmail.prompts import GMAIL_DRAFT_INSTRUCTIONS
from grievances.models import ComplaintDraft, CaseResearch, CaseResearchBrief
from grievances.mutations import CaseMutation
from grievances.store import CaseView, SelectionView


class EmailQuote(BaseModel):
    message_id: str
    quote: str = Field(min_length=1, max_length=2000)


class EmailDraft(BaseModel):
    subject: str = Field(min_length=1, max_length=constants.DRAFT_MAX_SUBJECT_CHARS)
    body: str = Field(min_length=1, max_length=constants.DRAFT_MAX_BODY_CHARS)
    sources: list[EmailQuote] = Field(default_factory=list, max_length=20)
    question: str = Field(default='', max_length=400)


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

    async def run(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                  row: CorrespondenceSnapshot, usage: RunUsage, recipient: str = '') -> str:
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
            source = incoming[-1] if incoming else latest
            choices = self.addresses((source.reply_to or source.sender) if incoming and source else source.recipient if source else '')
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
            policy = await self.policy_snapshot(turn, current)
            previous = await ComplaintDraft.objects.filter(case_id=current.id, case__owner_id=turn.owner_id).order_by('-version').afirst()
            result = await self.writer.run(json.dumps({'request': {'original_goal': row.pending_goal or turn.text, 'owner_reply': turn.text}, 'facts': current.facts,
                'reviewed_policy': policy, 'emails': [asdict(item) for item in row.messages], 'truncated': row.truncated,
                'previous_application_draft': {'version': previous.version, 'subject': previous.subject, 'body': previous.body} if previous else None}), usage=usage,
                usage_limits=UsageLimits(request_limit=constants.ASSISTANCE_REQUEST_LIMIT),
                timeout=constants.ASSISTANCE_TIMEOUT_SECONDS)
            content = result.output
            if content.question:
                return content.question
            sources = {message.message_id: message.subject + '\n' + message.text for message in row.messages}
            if sources and (not content.sources or any(quote.message_id not in sources or
                    quote.quote not in sources[quote.message_id] for quote in content.sources)):
                return 'I could not verify the draft against the emails. No Gmail draft was created.'
            envelope = DraftEnvelope(row.mailbox, target, latest.subject if latest else content.subject,
                content.body, row.selected_thread, latest.rfc_message_id if latest else '', latest.references if latest else '')
            evidence = self.fingerprint([asdict(item) for item in row.messages])
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
            with self.mailbox() as mailbox, tracing.operation('gmail.create_draft', {}, private=True):
                gmail_id = await sync_to_async(mailbox.create_draft)(envelope)
            await self.finish(export_id, 'created', gmail_id)
            await self.clear_pending(turn, selection, current)
            return await self.result(export_id)
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
            return (f'Draft v{row.draft.version} is ready in Gmail for {row.recipient}. Nothing was sent.\n'
                f'Open Gmail Drafts in {row.mailbox} and look for “{row.draft.subject}”.')
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
            export = GmailDraftExport.objects.create(draft=draft, mailbox=envelope.mailbox, recipient=envelope.recipient,
                thread_id=envelope.thread_id, fingerprint=fingerprint)
            return export.pk, True

    @classmethod
    def check_evidence(cls, current: CaseView, thread: str, evidence: str) -> None:
        mailbox = CaseMailbox.objects.filter(case_id=current.id).first()
        if mailbox is None or mailbox.selected_thread != thread or cls.fingerprint(mailbox.messages) != evidence:
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
    def finish(identifier: int, status: str, gmail_id: str = '') -> None:
        GmailDraftExport.objects.filter(pk=identifier).update(status=status, gmail_id=gmail_id)

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
        with self.mailbox() as mailbox, tracing.operation('gmail.reconcile_draft', {}, private=True):
            found = await sync_to_async(mailbox.find_export)(row.mailbox, await self.marker(identifier))
        if found:
            await self.finish(identifier, 'created', found)

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

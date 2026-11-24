"""Translate an ordinary case goal into private mail evidence and a useful reply."""
import json

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.tracing import tracing
from core.turns import OwnerTurn
from gmail import constants
from gmail.correspondence import Correspondence
from gmail.drafts import GmailDraftService
from gmail.records import CorrespondenceSnapshot
from gmail.prompts import MAIL_BRIEF_INSTRUCTIONS, MAIL_MATCH_INSTRUCTIONS
from grievances.schema import IntakeAction
from grievances.store import CaseStore, CaseView, SelectionView


class MailMatch(BaseModel):
    reference: str = Field(default='', max_length=128)
    question: str = Field(default='', max_length=400)


class CaseAssistance:
    def __init__(self, model: Model, store: CaseStore, correspondence: Correspondence) -> None:
        self.model = model
        self.store = store
        self.correspondence = correspondence
        self.matcher = Agent(model, name='gmail_thread_matcher', output_type=MailMatch,
            instructions=MAIL_MATCH_INSTRUCTIONS, retries=constants.PLANNER_RETRIES)
        self.writer = Agent(model, name='case_assistance', instructions=MAIL_BRIEF_INSTRUCTIONS,
            model_settings={'max_tokens': constants.ASSISTANCE_OUTPUT_TOKENS})
        self.matcher.instrument = tracing.private_instrumentation()
        self.writer.instrument = tracing.private_instrumentation()

    async def run(self, turn: OwnerTurn, action: IntakeAction, selection: SelectionView,
                  current: CaseView | None, usage: RunUsage) -> str:
        if not action.intent_quote or action.intent_quote not in turn.text:
            return 'Which problem would you like help with?'
        target = await self.store.get_case(turn.owner_id, action.case_id) if action.case_id else None
        if target is None and not action.case_id and action.label.strip() and action.label in turn.text:
            child = turn.followup()
            await self.store.apply(child, IntakeAction(action='create', label=action.label,
                intent_quote=action.intent_quote, facts=action.facts), selection, current)
            refreshed = await self.store.selection(turn.owner_id, turn.conversation_id)
            if refreshed.deletion_epoch != selection.deletion_epoch or not refreshed.case_id:
                return 'The case changed while I was working. Please try again.'
            target = await self.store.get_case(turn.owner_id, refreshed.case_id)
            if target is None or ' '.join(target.label.casefold().split()) != ' '.join(action.label.casefold().split()):
                return 'I could not open that problem without changing existing information. Which booking or purchase is this about?'
            selection = refreshed
        if target is None:
            return 'Which company or problem is this about?'
        if selection.case_id != target.id:
            # A separate receipt prevents the intermediate selection replay from replacing the answer.
            child = turn.followup()
            await self.store.apply(child, IntakeAction(action='select', case_id=target.id,
                intent_quote=action.intent_quote), selection, current)
            refreshed = await self.store.selection(turn.owner_id, turn.conversation_id)
            if refreshed.case_id != target.id or refreshed.deletion_epoch != selection.deletion_epoch:
                return 'The case changed while I was working. Please try again.'
            selection = refreshed
        current = target
        if action.gmail_revision and action.facts:
            saved = await self.store.apply(turn.followup().followup(), IntakeAction(action='update',
                case_id=current.id, facts=action.facts, correction_quote=action.correction_quote), selection, current)
            refreshed_case = await self.store.get_case(turn.owner_id, current.id)
            if refreshed_case is None or any(refreshed_case.facts.get(f.key) != f.value for f in action.facts):
                return saved
            current = refreshed_case
        if not await self.correspondence.consented(turn.owner_id):
            await self.correspondence.remember_goal(turn, selection, current)
            return await self.correspondence.run(turn, 'gmail_search', selection, current)
        return await self.correspondence.assist(turn, selection, current, usage, self,
            draft=action.gmail_draft or action.gmail_revision, recipient=action.gmail_recipient,
            revision=action.gmail_revision, version=action.draft_version)

    async def draft(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                    row: CorrespondenceSnapshot, usage: RunUsage, recipient: str,
                    revision: bool = False, version: int | None = None) -> str:
        if self.correspondence.mailbox is None:
            return 'Gmail is unavailable. Please check the connection.'
        answer = await GmailDraftService(self.model, self.correspondence.mailbox).run(
            turn, selection, current, row, usage, recipient, revision=revision, version=version)
        if answer.rstrip().endswith('?'):
            await self.correspondence.remember_goal(turn, selection, current, answer, recipient)
        return answer

    async def match(self, request: str, case: CaseView, candidates: list[dict[str, str]],
                    usage: RunUsage) -> MailMatch:
        result = await self.matcher.run(json.dumps({'request': request, 'case': case.label,
            'identifiers': {k: v for k, v in case.facts.items() if k in ('company', 'order_reference')},
            'previews': candidates}), usage=usage,
            usage_limits=UsageLimits(request_limit=constants.ASSISTANCE_REQUEST_LIMIT),
            timeout=constants.ASSISTANCE_TIMEOUT_SECONDS)
        return result.output

    async def explain(self, request: str, case: CaseView, messages: list[dict[str, object]],
                      truncated: bool, usage: RunUsage) -> str:
        result = await self.writer.run(json.dumps({'request': request, 'case': case.label,
            'recorded_facts': case.facts, 'emails': messages, 'truncated': truncated}), usage=usage,
            usage_limits=UsageLimits(request_limit=constants.ASSISTANCE_REQUEST_LIMIT),
            timeout=constants.ASSISTANCE_TIMEOUT_SECONDS)
        return result.output

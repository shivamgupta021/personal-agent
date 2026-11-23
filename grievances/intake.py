"""Interpret private requests and authorize proposals before capability dispatch."""

from dataclasses import asdict, dataclass, replace
import json
import re

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.tracing import tracing
from core.turns import OwnerTurn
from grievances import constants
from grievances.correspondence import CompanyReplyParser, ParsedCompanyReply
from grievances.policy_store import PolicyStore
from grievances.progress_store import ProgressSnapshot, ProgressStore
from grievances.prompts import INTAKE_INSTRUCTIONS, SELECTED_INTAKE_INSTRUCTIONS
from grievances.schema import IntakeAction
from grievances.store import CaseStore, CaseView, SelectionView
from preferences.store import PreferenceStore


DRAFT_ACTIONS = frozenset(('draft', 'list_drafts', 'read_draft'))
POLICY_ACTIONS = frozenset(('review_research', 'confirm_research', 'research'))
PROGRESS_ACTIONS = frozenset(('record_sent', 'record_reply', 'resolve', 'close_without_resolution', 'follow_up'))
SELECTED_ACTIONS = frozenset(('show', 'update')) | DRAFT_ACTIONS | POLICY_ACTIONS | PROGRESS_ACTIONS
GENERAL_ACTIONS = frozenset(('conversation', 'memory', 'public'))


@dataclass(frozen=True)
class AuthorizedIntake:
    """A validated proposal and the exact snapshot/budget its handlers must use.

    Storage still checks revisions and handler-specific authorization at mutation time.
    """

    action: IntakeAction
    selection: SelectionView
    current: CaseView | None
    usage: RunUsage
    company_reply: ParsedCompanyReply | None


@dataclass(frozen=True)
class _IntakeContext:
    selection: SelectionView
    current: CaseView | None
    directory: dict[str, object]
    progress: ProgressSnapshot | None

    def selected_payload(self, request: str) -> dict[str, object]:
        if self.current is None:
            raise ValueError('Selected intake requires a selected case')
        messages: list[str] = []
        size = len(json.dumps(self.current.facts)) + len(request)
        for message in reversed(self.current.messages):
            if size + len(message) > constants.MAX_CONTEXT_CHARS // 2:
                break
            messages.insert(0, message)
            size += len(message)
        return {
            'current_request': request,
            'research_status': self.directory['research_status'],
            'draft_versions': self.directory['draft_versions'],
            'progress': asdict(self.progress) if self.progress else None,
            'selected_case': {'id': self.current.id, 'facts': self.current.facts, 'messages': messages},
            'saved_preferences': self.directory['saved_preferences'],
        }


class CaseIntake:
    """Own directory/selected interpretation and current-message authorization.

    Case facts enter model context only after a selected-case route. Each call owns
    its usage budget, shared with subsequent drafting or policy research.
    """

    def __init__(self, model: Model, store: CaseStore) -> None:
        self.store = store
        self.agent: Agent[None, IntakeAction] = Agent(
            model, output_type=IntakeAction, instructions=INTAKE_INSTRUCTIONS,
            name='case_intake', retries=constants.VALIDATION_RETRIES,
            model_settings={'max_tokens': constants.MAX_OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()

    async def interpret(self, turn: OwnerTurn, epoch: int) -> AuthorizedIntake | str:
        if len(turn.text) > constants.MAX_INPUT_CHARS:
            return 'Please send a shorter case message. Nothing changed.'
        company_reply = CompanyReplyParser().parse(turn.text)
        context = await self._context(turn, epoch)
        encoded = json.dumps(context.directory)
        if len(encoded) > constants.MAX_CONTEXT_CHARS:
            return 'This case directory exceeds the intake context limit. Nothing changed.'
        usage = RunUsage()
        with tracing.operation('case.intake', context.directory, kind='agent', private=True) as span:
            result = await self.agent.run(encoded, usage=usage,
                usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS),
                timeout=constants.RUN_TIMEOUT_SECONDS)
            span.set_attribute('application.route', result.output.action)
            tracing.record_output(span, result.output.model_dump(mode='json'))
        action = result.output
        if action.action in SELECTED_ACTIONS and context.current:
            selected_payload = context.selected_payload(turn.text)
            selected_encoded = json.dumps(selected_payload)
            if len(selected_encoded) > constants.MAX_CONTEXT_CHARS:
                return 'This case exceeds the intake context limit. Nothing changed.'
            with tracing.operation('case.selected_intake', selected_payload, kind='agent', private=True) as span:
                action = (await self.agent.run(selected_encoded, instructions=SELECTED_INTAKE_INSTRUCTIONS,
                    usage=usage, usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS),
                    timeout=constants.RUN_TIMEOUT_SECONDS)).output
                span.set_attribute('application.route', action.action)
                tracing.record_output(span, action.model_dump(mode='json'))
            if action.action not in SELECTED_ACTIONS and action.action != 'clarify':
                return 'Please clarify the intended case action. Nothing changed.'
        rejection = self._authorize(turn.text, action, context.selection, company_reply)
        if rejection is not None:
            return rejection
        return AuthorizedIntake(action, context.selection, context.current, usage, company_reply)

    async def _context(self, turn: OwnerTurn, epoch: int) -> _IntakeContext:
        selection = replace(await self.store.selection(turn.owner_id, turn.conversation_id), deletion_epoch=epoch)
        current = await self.store.get_case(turn.owner_id, selection.case_id) if selection.case_id else None
        brief = await PolicyStore().latest_brief(turn.owner_id, current.id) if current else None
        research_status = {'reviewed': brief is not None, 'confirmed': bool(brief and brief.confirmed),
                           'revision': brief.revision if brief else None,
                           'matches_current_facts': bool(brief and current and brief.fact_revision == current.revision)}
        progress = await ProgressStore().snapshot(turn.owner_id, current.id) if current else None
        draft_versions = [draft.version for draft in await self.store.list_drafts(turn.owner_id, current.id)] if current else []
        cases = await self.store.list_cases(turn.owner_id)
        preferences = await PreferenceStore().list(turn.owner_id)
        directory: dict[str, object] = {
            'current_request': turn.text, 'research_status': research_status, 'draft_versions': draft_versions,
            'selected_case': {'id': current.id} if current else None,
            'case_labels': [asdict(case) for case in cases],
            'saved_preferences': [{'key': p.key, 'value': p.value} for p in preferences],
        }
        return _IntakeContext(selection, current, directory, progress)

    @staticmethod
    def _authorize(request: str, action: IntakeAction, selection: SelectionView,
                   company_reply: ParsedCompanyReply | None) -> str | None:
        if company_reply is not None and action.action not in ('record_reply', 'clarify'):
            return 'Company correspondence cannot authorize case changes or sending. Please save the quoted reply first; nothing was changed.'
        if any(f.quote not in request or f.value not in f.quote for f in action.facts):
            return 'I could not verify the supplied facts. Nothing changed; please state them explicitly.'
        if action.action == 'delete':
            return 'Please explicitly say “Delete this case” or “Delete case LABEL”. Nothing changed.'
        if action.action in ('create', 'select', 'leave') and (not action.intent_quote or action.intent_quote not in request):
            return 'Please explicitly state the case action. Nothing changed.'
        if action.action == 'create' and (not action.label.strip() or action.label not in request
                or len(' '.join(action.label.casefold().split())) > 120):
            return 'Please supply a short label for the new case. Nothing changed.'
        if action.correction_quote and (action.correction_quote not in request or not re.search(
                r'\b(correct(?:ion)?|replace|change|update|amend)\b', action.correction_quote, re.IGNORECASE)):
            return 'Please explicitly state the correction. Nothing changed.'
        if (action.action in SELECTED_ACTIONS or action.action.startswith('gmail_')) and action.case_id and action.case_id != selection.case_id:
            return 'Please explicitly select the intended case first. Nothing changed.'
        return None

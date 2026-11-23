"""Route private intake before any public conversation history or web-enabled agent."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
import json
import re

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded, RunCancelled
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.errors import ReplySavedDraftFailed
from core.turns import OwnerTurn, PreparedReply
from grievances.ownership import CaseOwnership
from grievances.deletion import CaseDeletionService
from grievances.cleanup import CaseTraceCleanup
from core.tracing import tracing
from grievances import constants
from grievances.correspondence import CompanyReplyParser
from grievances.drafts import DraftService
from grievances.policy import PolicyResearchService, PublicResearchBoundary
from grievances.policy_store import PolicyStateChanged, PolicyStore
from research.tools.firecrawl import Firecrawl
from grievances.progress_store import ProgressChanged, ProgressStore
from grievances.prompts import INTAKE_INSTRUCTIONS, SELECTED_INTAKE_INSTRUCTIONS
from grievances.schema import IntakeAction
from grievances.store import CaseStore
from preferences.store import PreferenceStore
from subscriptions.service import SubscriptionService
from conversations.constants import DELETE_CONVERSATION_PHRASES
from conversations.service import ConversationService
from conversations.memory import ConversationMemoryRunner


class ApplicationService:
    def __init__(self, model: Model, public_run: Callable[[str], Awaitable[str]] | None = None, *, firecrawl: Firecrawl | None = None, trace_cleanup: CaseTraceCleanup | None = None) -> None:
        self.store = CaseStore()
        self.deletion = CaseDeletionService(trace_cleanup)
        self.firecrawl = firecrawl
        self.model = model
        self.public_run = public_run
        self.agent: Agent[None, IntakeAction] = Agent(model, output_type=IntakeAction,
            instructions=INTAKE_INSTRUCTIONS, name='case_intake', retries=constants.VALIDATION_RETRIES,
            model_settings={'max_tokens': constants.MAX_OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()

    async def run(self, turn: OwnerTurn) -> str:
        return await (await self.prepare(turn)).render()

    async def delete_case(self, turn: OwnerTurn, case_id: str) -> str:
        return (await self.deletion.delete(turn, case_id)).message

    async def prepare(self, turn: OwnerTurn) -> PreparedReply:
        with tracing.operation('application.turn', {'message': turn.text}, kind='agent', private=True) as span:
            span.set_attribute('langfuse.session.id', turn.owner_id)
            reply = await self._prepare(turn)
            tracing.record_output(span, {'answer': await reply.render()})
            return reply

    async def _prepare(self, turn: OwnerTurn) -> PreparedReply:
        epoch = await CaseOwnership().begin(turn)
        if epoch != await CaseOwnership().epoch(turn.owner_id):
            text = 'This delivery predates case deletion; nothing was recreated. Please send a new request.'
        else:
            control = await self.deletion.control(turn)
            if control is None:
                text = await self._execute(turn, epoch)
            else:
                text = control.message
                if control.deleted:
                    epoch = await CaseOwnership().epoch(turn.owner_id)
        async def valid() -> bool:
            return epoch == await CaseOwnership().epoch(turn.owner_id)
        return PreparedReply(text, valid)

    async def _execute(self, turn: OwnerTurn, epoch: int) -> str:
        try:
            async with asyncio.timeout(constants.RUN_TIMEOUT_SECONDS):
                return await self._run(turn, epoch)
        except (ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded, RunCancelled, TimeoutError):
            if await ProgressStore().reply_saved(turn):
                raise ReplySavedDraftFailed from None
            raise

    async def _run(self, turn: OwnerTurn, epoch: int) -> str:
        if self.is_memory_control(turn.text):
            return await self.memory(turn)
        replay = await self.store.replay(turn)
        if replay:
            return replay
        if len(turn.text) > constants.MAX_INPUT_CHARS:
            return 'Please send a shorter case message. Nothing changed.'
        company_reply = CompanyReplyParser().parse(turn.text)
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
        payload = {'current_request': turn.text, 'research_status': research_status, 'draft_versions': draft_versions,
                   'selected_case': {'id': current.id} if current else None,
                   'case_labels': [asdict(case) for case in cases],
                   'saved_preferences': [{'key': p.key, 'value': p.value} for p in preferences]}
        encoded = json.dumps(payload)
        if len(encoded) > constants.MAX_CONTEXT_CHARS:
            return 'This case directory exceeds the intake context limit. Nothing changed.'
        usage = RunUsage()
        with tracing.operation('case.intake', payload, kind='agent', private=True) as span:
            result = await self.agent.run(encoded, usage=usage, usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS),
                                          timeout=constants.RUN_TIMEOUT_SECONDS)
            span.set_attribute('application.route', result.output.action)
            tracing.record_output(span, result.output.model_dump(mode='json'))
        action = result.output
        if action.action in ('show', 'update', 'draft', 'list_drafts', 'read_draft', 'review_research', 'confirm_research', 'research', 'record_sent', 'record_reply', 'resolve', 'close_without_resolution', 'follow_up') and current:
            messages: list[str] = []
            size = len(json.dumps(current.facts)) + len(turn.text)
            for message in reversed(current.messages):
                if size + len(message) > constants.MAX_CONTEXT_CHARS // 2:
                    break
                messages.insert(0, message)
                size += len(message)
            selected_payload = json.dumps({'current_request': turn.text, 'research_status': research_status,
                'draft_versions': draft_versions, 'progress': asdict(progress) if progress else None,
                'selected_case': {'id': current.id, 'facts': current.facts, 'messages': messages},
                'saved_preferences': [{'key': p.key, 'value': p.value} for p in preferences]})
            if len(selected_payload) > constants.MAX_CONTEXT_CHARS:
                return 'This case exceeds the intake context limit. Nothing changed.'
            with tracing.operation('case.selected_intake', {}, kind='agent', private=True):
                action = (await self.agent.run(selected_payload, instructions=SELECTED_INTAKE_INSTRUCTIONS, usage=usage,
                    usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS),
                    timeout=constants.RUN_TIMEOUT_SECONDS)).output
            if action.action not in ('show', 'update', 'draft', 'list_drafts', 'read_draft', 'review_research', 'confirm_research', 'research', 'record_sent', 'record_reply', 'resolve', 'close_without_resolution', 'follow_up', 'clarify'):
                return 'Please clarify the intended case action. Nothing changed.'
        if company_reply is not None and action.action not in ('record_reply', 'clarify'):
            return 'Company correspondence cannot authorize case changes or sending. Please save the quoted reply first; nothing was changed.'
        if any(f.quote not in turn.text or f.value not in f.quote for f in action.facts):
            return 'I could not verify the supplied facts. Nothing changed; please state them explicitly.'
        if action.action == 'delete':
            return 'Please explicitly say “Delete this case” or “Delete case LABEL”. Nothing changed.'
        if action.action in ('create', 'select', 'leave') and (not action.intent_quote or action.intent_quote not in turn.text):
            return 'Please explicitly state the case action. Nothing changed.'
        if action.action == 'create' and (not action.label.strip() or action.label not in turn.text
                or len(' '.join(action.label.casefold().split())) > 120):
            return 'Please supply a short label for the new case. Nothing changed.'
        if action.correction_quote and (action.correction_quote not in turn.text or not re.search(
                r'\b(correct(?:ion)?|replace|change|update|amend)\b', action.correction_quote, re.IGNORECASE)):
            return 'Please explicitly state the correction. Nothing changed.'
        if action.action in ('show', 'update', 'draft', 'list_drafts', 'read_draft', 'review_research', 'confirm_research', 'research', 'record_sent', 'record_reply', 'resolve', 'close_without_resolution', 'follow_up') and action.case_id and action.case_id != selection.case_id:
            return 'Please explicitly select the intended case first. Nothing changed.'
        if action.action in ('record_sent', 'record_reply', 'resolve', 'close_without_resolution', 'follow_up'):
            if current is None:
                return 'Please select the intended case before reporting progress.'
            if action.facts or action.correction_quote:
                return 'Please save fact corrections separately before reporting sending.'
            try:
                if action.action == 'follow_up':
                    return await DraftService(self.model, self.store).run(turn, selection, current, usage, follow_up=True)
                if action.action == 'record_reply':
                    saved = await ProgressStore().record_reply(turn, selection, current, action.reply_text, company_reply)
                    if not saved.saved or not action.draft_follow_up:
                        return saved.message
                    if company_reply is None or not company_reply.requests_followup():
                        return saved.message + ' Request a follow-up separately in your own words if needed.'
                    refreshed = await self.store.get_case(turn.owner_id, current.id)
                    if refreshed is None:
                        return 'Company reply was saved, but the case is no longer available. No follow-up was saved.'
                    child = await CaseOwnership().followup(turn)
                    draft_reply = await DraftService(self.model, self.store).run(child, selection, refreshed, usage, follow_up=True)
                    return saved.message + '\n' + draft_reply
                if action.action in ('resolve', 'close_without_resolution'):
                    outcome = 'resolved' if action.action == 'resolve' else 'closed_without_resolution'
                    return await ProgressStore().record_outcome(turn, selection, current, outcome, action.intent_quote)
                sent = await ProgressStore().record_sent(turn, selection, current, action.draft_version,
                                                         action.intent_quote, action.sent_details)
                if sent.saved and sent.version is not None:
                    draft = await self.store.get_draft(turn.owner_id, current.id, sent.version)
                    if draft and draft.outdated:
                        return sent.message + ' That recorded version is outdated; its original text is preserved.'
                return sent.message
            except ProgressChanged:
                return 'Case state changed while recording progress. Nothing was saved; please try again.'
        if action.action in ('review_research', 'confirm_research', 'research'):
            if current is None:
                return 'Please select the intended case before policy research.'
            if action.facts or action.correction_quote:
                return 'Please save case fact changes separately before reviewing or researching public policies.'
            policy_store = PolicyStore()
            try:
                if action.action == 'review_research':
                    if action.public_brief is None or not await PublicResearchBoundary(turn.owner_id, self.store).validate_brief(action.public_brief):
                        return 'Please supply only public company identity, product category, issue category, jurisdiction and the official HTTPS site root for review. No web request was sent.'
                    return await policy_store.propose(turn, selection, current, action.public_brief)
                if action.action == 'confirm_research':
                    if not re.fullmatch(r'i confirm (?:these |the )?public research fields and (?:this |the )?official site[.!]?', turn.text.strip(), re.IGNORECASE):
                        return 'To confirm the displayed values, explicitly say: I confirm these public research fields and this official site. Nothing was sent to the web.'
                    return await policy_store.confirm(turn, selection, current)
                return await PolicyResearchService(self.model, self.store, self.firecrawl).run(turn, selection, current, usage)
            except PolicyStateChanged:
                return 'Case facts, selection or public research fields changed. No research changes were saved; please review them again.'
        if action.action in ('draft', 'list_drafts', 'read_draft'):
            if current is None:
                return 'Please select the intended case before requesting drafts.'
            if action.facts or action.correction_quote:
                return 'Please save or correct the case facts first, then request the draft. No draft was saved.'
            if action.action == 'draft':
                return await DraftService(self.model, self.store).run(turn, selection, current, usage)
            return await self.store.read_drafts(turn, selection, current, action.draft_version, listing=action.action == 'list_drafts')
        if action.action in ('conversation', 'memory', 'public') and not await self.store.general_allowed(turn, epoch):
            return 'Case material cannot become general memory or public research. Please send a new independent request.'
        if action.action in ('conversation', 'memory'):
            if action.facts:
                return 'Case facts cannot become general memory. Please clarify the preference or conversation information to manage.'
            return await self.memory(turn)
        if action.action == 'public':
            if current or action.facts:
                return 'Please explicitly return to general research and send a public question separately, or select a case.'
            return await self.public_run(turn.text) if self.public_run else 'Public research is unavailable in this session.'
        return await self.store.apply(turn, action, selection, current)

    @staticmethod
    def is_memory_control(message: str) -> bool:
        control = message.strip().rstrip('.!').casefold()
        return control in DELETE_CONVERSATION_PHRASES or control == 'retry trace cleanup'

    async def memory(self, turn: OwnerTurn) -> str:
        with tracing.operation('application.memory', {'message': turn.text}, kind='agent', private=True) as span:
            answer = await ConversationService(turn.owner_id, ConversationMemoryRunner(self.model)).run(turn.text)
            tracing.record_output(span, {'answer': answer})
            return answer


class PublicResearchRequested(Exception):
    """Unwind the private subscription session before entering public research."""


class SubscriptionApplicationService:
    """Open the subscription transport for one normalized application turn."""

    def __init__(self, subscription: SubscriptionService, public_run: Callable[[str], Awaitable[str]]) -> None:
        self.subscription = subscription
        self.public_run = public_run

    async def run(self, turn: OwnerTurn) -> str:
        return await (await self.prepare(turn)).render()

    async def prepare(self, turn: OwnerTurn) -> PreparedReply:
        epoch = await CaseOwnership().begin(turn)
        async def valid() -> bool:
            return epoch == await CaseOwnership().epoch(turn.owner_id)
        if epoch != await CaseOwnership().epoch(turn.owner_id):
            return PreparedReply('This delivery predates deletion; nothing was recreated.', valid)
        control = await CaseDeletionService().control(turn)
        if control is not None:
            if control.deleted:
                epoch = await CaseOwnership().epoch(turn.owner_id)
            return PreparedReply(control.message, valid)
        if ApplicationService.is_memory_control(turn.text):
            with tracing.operation('application.memory', {}, kind='agent', private=True):
                return PreparedReply(await self.public_run(turn.text), valid)
        try:
            async with self.subscription.model() as model:
                return await ApplicationService(model, self.defer_public).prepare(turn)
        except PublicResearchRequested:
            if not await valid():
                return PreparedReply('Case content was deleted. Please send a new public request.', valid)
            return PreparedReply(await self.public_run(turn.text), valid)

    async def defer_public(self, message: str) -> str:
        raise PublicResearchRequested

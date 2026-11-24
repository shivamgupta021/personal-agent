"""Route private intake before any public conversation history or web-enabled agent."""

import asyncio
from dataclasses import replace
from collections.abc import Awaitable, Callable
import re


from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded, RunCancelled
from pydantic_ai.models import Model

from gmail.assistance import CaseAssistance
from gmail.onboarding import GmailConnectionRequested, ConnectionAttempt
from gmail.drafts import GmailDraftService
from gmail.correspondence import Correspondence, CONSENT_MESSAGE
from gmail.factory import GmailFactory
from gmail.planner import GmailQueryPlanner
from gmail.reply_checks import GmailReplyChecks
from gmail.monitoring_store import MonitoringStore
from gmail.reminder_store import ReminderStore
from django.utils import timezone
from core.errors import ReplySavedDraftFailed
from core.turns import OwnerTurn, PreparedReply
from grievances.ownership import CaseOwnership
from grievances.deletion import CaseDeletionService
from grievances.cleanup import CaseTraceCleanup
from core.tracing import tracing
from preferences.inspection import PreferenceInspection
from grievances import constants
from grievances.drafts import DraftService
from grievances.policy import PolicyResearchService, PublicResearchBoundary
from grievances.policy_store import PolicyStateChanged, PolicyStore
from research.tools.firecrawl import Firecrawl
from grievances.progress_store import ProgressChanged, ProgressStore
from grievances.schema import IntakeAction
from grievances.store import CaseStore
from grievances.intake import CaseIntake, DRAFT_ACTIONS, POLICY_ACTIONS, PROGRESS_ACTIONS, GENERAL_ACTIONS
from grievances.lifecycle import ApplicationTurn
from subscriptions.service import SubscriptionService
from conversations.constants import DELETE_CONVERSATION_PHRASES
from conversations.service import ConversationService
from conversations.memory import ConversationMemoryRunner


class ApplicationService:
    def __init__(self, model: Model, public_run: Callable[[str], Awaitable[str]] | None = None, *, firecrawl: Firecrawl | None = None, trace_cleanup: CaseTraceCleanup | None = None, correspondence: Correspondence | None = None) -> None:
        self.correspondence = correspondence or Correspondence()
        self.store = CaseStore()
        self.deletion = CaseDeletionService(trace_cleanup)
        self.firecrawl = firecrawl
        self.model = model
        self.public_run = public_run
        self.intake = CaseIntake(model, self.store)
        self.turns = ApplicationTurn(trace_cleanup)

    async def run(self, turn: OwnerTurn) -> str:
        return await (await self.prepare(turn)).render()

    async def delete_case(self, turn: OwnerTurn, case_id: str) -> str:
        return (await self.deletion.delete(turn, case_id)).message

    async def prepare(self, turn: OwnerTurn) -> PreparedReply:
        return await self.turns.prepare(turn, self._execute)

    async def _execute(self, turn: OwnerTurn, epoch: int) -> str:
        try:
            async with asyncio.timeout(constants.RUN_TIMEOUT_SECONDS):
                return await self._run(turn, epoch)
        except (ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded, RunCancelled, TimeoutError):
            if await ProgressStore().reply_saved(turn):
                raise ReplySavedDraftFailed from None
            raise

    async def _run(self, turn: OwnerTurn, epoch: int) -> str:
        if turn.text.strip().rstrip('.!').casefold() == 'i agree to email processing':
            accepted = await self.correspondence.accept(turn.owner_id)
            pending = await self.correspondence.pending_goal(turn)
            return await self._run(replace(turn, text=pending), epoch) if pending else accepted
        if self.is_memory_control(turn.text):
            return await self.memory(turn)
        replay = await GmailDraftService.replay(turn) or await self.store.replay(turn)
        if replay:
            return replay
        intake = await self.intake.interpret(turn, epoch)
        if isinstance(intake, str):
            return intake
        action, selection, current = intake.action, intake.selection, intake.current
        usage, company_reply = intake.usage, intake.company_reply
        if action.action == 'list_preferences':
            return await PreferenceInspection().run(turn)
        if action.action == 'gmail_connect':
            if (not action.gmail_account or action.gmail_account not in turn.text
                    or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', action.gmail_account)
                    or not action.intent_quote or action.intent_quote not in turn.text):
                return 'Which Gmail address should I connect? Reply with “Connect Gmail for your-address@gmail.com”.'
            if action.gmail_compose and not re.search(r'\b(draft|drafts|compose)\b', turn.text, re.IGNORECASE):
                return 'Do you want read-only access, or also permission to create Gmail drafts?'
            raise GmailConnectionRequested(action.gmail_account, action.gmail_compose)
        if action.action.startswith('gmail_reminder_'):
            if action.case_id and action.case_id != selection.case_id:
                target = await self.store.get_case(turn.owner_id, action.case_id)
                if target is None or target.label.casefold() not in turn.text.casefold():
                    return 'Which case is this reminder for? Please name it explicitly.'
                await self.store.apply(turn.followup().followup(), IntakeAction(action='select',
                    case_id=target.id, intent_quote=turn.text), selection, current)
                reminder_selection = await self.store.selection(turn.owner_id, turn.conversation_id)
                if reminder_selection.case_id != target.id or reminder_selection.deletion_epoch != selection.deletion_epoch:
                    return 'The case changed. Please try again.'
                selection, current = reminder_selection, target
            if current is None:
                return 'Which case is this reminder for?'
            now = timezone.now()
            error = ReminderStore.deadline_error(action, turn.text, now)
            if error:
                return error
            if action.action == 'gmail_reminder_set':
                if self.correspondence.mailbox is None:
                    return 'Gmail is unavailable; I could not verify correspondence for this reminder.'
                checked = await GmailReplyChecks(self.model, self.correspondence.mailbox).check(
                    turn.followup().followup().followup(), selection, current, usage=usage, reuse_analysis=True)
                if not checked.verified:
                    return 'I could not check the selected correspondence. No reminder was scheduled; please try again.'
                await MonitoringStore().control(turn.followup(), selection, current, 'gmail_monitor_start', now)
            return await ReminderStore().control(turn, selection, current, action, now)
        if action.action in ('gmail_monitor_start', 'gmail_monitor_stop', 'gmail_monitor_status'):
            if current is None:
                return 'Which case should I monitor?'
            return await MonitoringStore().control(turn, selection, current, action.action, timezone.now())
        if action.action == 'case_help':
            return await CaseAssistance(self.model, self.store, self.correspondence).run(
                turn, action, selection, current, usage)
        if action.action == 'gmail_open_draft':
            if current is None or self.correspondence.mailbox is None:
                return 'Which case’s Gmail draft should I open?'
            return await GmailDraftService(self.model, self.correspondence.mailbox).open(
                turn, selection, current, action.draft_version)
        if action.action == 'gmail_check_replies':
            if current is None:
                return 'Which case should I check for replies?'
            if self.correspondence.mailbox is None:
                return 'Gmail access is unavailable in this session. Check the Gmail connection.'
            return await GmailReplyChecks(self.model, self.correspondence.mailbox).run(
                turn, selection, current, usage)
        if action.action.startswith('gmail_'):
            return await self.correspondence.run(turn, action.action, selection, current,
                action.gmail_reference, usage, constants.MAX_MODEL_REQUESTS)
        if action.action in PROGRESS_ACTIONS:
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
        if action.action in POLICY_ACTIONS:
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
        if action.action in DRAFT_ACTIONS:
            if current is None:
                return 'Please select the intended case before requesting drafts.'
            if action.facts or action.correction_quote:
                return 'Please save or correct the case facts first, then request the draft. No draft was saved.'
            if action.action == 'draft':
                return await DraftService(self.model, self.store).run(turn, selection, current, usage)
            return await self.store.read_drafts(turn, selection, current, action.draft_version, listing=action.action == 'list_drafts')
        if action.action in GENERAL_ACTIONS and not await self.store.general_allowed(turn, epoch):
            return 'Case material cannot become general memory or public research. Please send a new independent request.'
        if action.action in ('conversation', 'memory'):
            if action.facts:
                return 'Case facts cannot become general memory. Please clarify the preference or conversation information to manage.'
            return await self.memory(turn)
        if action.action == 'public':
            if current or action.facts:
                return 'Please explicitly return to general research and send a public question separately, or select a case.'
            with tracing.public_content():
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

    def __init__(self, subscription: SubscriptionService, public_run: Callable[[str], Awaitable[str]],
                 connect_gmail: Callable[[OwnerTurn, str, bool], Awaitable[ConnectionAttempt]] | None = None) -> None:
        self.subscription = subscription
        self.connect_gmail = connect_gmail
        self.public_run = public_run

    async def run(self, turn: OwnerTurn) -> str:
        return await (await self.prepare(turn)).render()

    async def prepare(self, turn: OwnerTurn) -> PreparedReply:
        return await ApplicationTurn().prepare(turn, self._execute)

    async def _execute(self, turn: OwnerTurn, epoch: int) -> str:
        if ApplicationService.is_memory_control(turn.text):
            return await self.public_run(turn.text)
        try:
            async with self.subscription.model() as model:
                return await ApplicationService(model, self.defer_public,
                    correspondence=Correspondence(GmailFactory.mailbox, GmailQueryPlanner(model), GmailFactory.require_connection))._execute(turn, epoch)
        except GmailConnectionRequested as request:
            if self.connect_gmail is None:
                return 'Gmail connection setup is available through the Telegram application on this Mac.'
            connected = await self.connect_gmail(turn, request.email, request.compose)
            if not connected.connected:
                return connected.message
            if epoch != await CaseOwnership().epoch(turn.owner_id):
                return connected.message + ' The case changed; please send a new request.'
            pending = await Correspondence().pending_goal(turn)
            if pending and await Correspondence.consented(turn.owner_id):
                resumed = await self._execute(replace(turn.followup(), text=pending), epoch)
                return connected.message + '\n\n' + resumed
            if pending:
                return connected.message + '\n\n' + CONSENT_MESSAGE
            return connected.message + ' Tell me which email or case you need help with.'
        except PublicResearchRequested:
            if epoch != await CaseOwnership().epoch(turn.owner_id):
                return 'Case content was deleted. Please send a new public request.'
            with tracing.public_content():
                return await self.public_run(turn.text)

    async def defer_public(self, message: str) -> str:
        raise PublicResearchRequested

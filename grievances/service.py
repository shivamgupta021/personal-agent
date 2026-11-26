"""Route private intake before any public conversation history or web-enabled agent."""

import asyncio
from dataclasses import replace
from collections.abc import Awaitable, Callable
import re


from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded, RunCancelled
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from gmail.assistance import CaseAssistance
from gmail.followups import FollowupService
from gmail.errors import GmailError
from attachments.reader import AttachmentReader
from gmail.onboarding import GmailConnectionRequested, ConnectionAttempt
from gmail.drafts import GmailDraftService
from gmail.correspondence import Correspondence, CONSENT_MESSAGE
from gmail.factory import GmailFactory
from gmail.planner import GmailQueryPlanner
from gmail.reply_checks import GmailReplyChecks
from gmail.monitoring_store import MonitoringStore
from gmail.reminder_store import ReminderStore
from django.utils import timezone
from core.actions import CaseAction
from asgiref.sync import sync_to_async
from gmail.errors import GmailDisconnected
from core.errors import ReplySavedDraftFailed, user_error_message
from core.turns import OwnerTurn, PreparedReply
from grievances.ownership import CaseOwnership
from grievances.deletion import CaseDeletionService
from grievances.cleanup import CaseTraceCleanup
from core.tracing import tracing
from preferences.inspection import PreferenceInspection
from preferences.store import PreferenceStore
from preferences.runner import PreferenceRunner
from grievances import constants
from grievances.drafts import DraftService
from grievances.policy import PolicyResearchService, PublicResearchBoundary
from grievances.policy_store import PolicyStateChanged, PolicyStore
from research.tools.firecrawl import Firecrawl
from grievances.progress_store import ProgressChanged, ProgressStore
from grievances.schema import IntakeAction
from grievances.store import CaseStore
from grievances.plan import CasePlan
from grievances.intake import CaseIntake, DRAFT_ACTIONS, POLICY_ACTIONS, PROGRESS_ACTIONS, GENERAL_ACTIONS
from grievances.lifecycle import ApplicationTurn
from subscriptions.service import SubscriptionService
from conversations.constants import DELETE_CONVERSATION_PHRASES
from conversations.service import ConversationService
from conversations.memory import ConversationMemoryRunner
from conversations.direct import DirectConversation


class ApplicationService:
    def __init__(self, model: Model, public_run: Callable[[str], Awaitable[str]] | None = None, *, firecrawl: Firecrawl | None = None, trace_cleanup: CaseTraceCleanup | None = None, correspondence: Correspondence | None = None, progress: Callable[[PreparedReply], Awaitable[None]] | None = None) -> None:
        self.plan = CasePlan(progress)
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
        if turn.callback:
            return await self.button(turn, epoch)
        if turn.uploads:
            return await AttachmentReader(self.model).run(turn, epoch)
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
        if action.action == 'read_attachment':
            return await AttachmentReader(self.model).run(turn, epoch)
        if action.action == 'gmail_connect':
            if (not action.gmail_account or action.gmail_account not in turn.text
                    or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', action.gmail_account)
                    or not action.intent_quote or action.intent_quote not in turn.text):
                return 'Which Gmail address should I connect?'
            raise GmailConnectionRequested(action.gmail_account, True)
        if action.action.startswith('gmail_followup_'):
            if current is None or self.correspondence.mailbox is None:
                return 'Please select the case and connect Gmail before planning automatic emails.'
            try:
                return await FollowupService(self.correspondence.mailbox).control(turn, selection, current, action, timezone.now())
            except GmailError as gmail_error:
                return str(gmail_error)
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
            action = await ReminderStore.resolve_kind(turn.owner_id, current.id, action)
            now = timezone.now()
            error = ReminderStore.deadline_error(action, turn.text, now)
            if error:
                return error
            if action.action == 'gmail_reminder_set' and action.reminder_kind == 'no_response':
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
            return await CaseAssistance(self.model, self.store, self.correspondence, self.plan).run(
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
        if action.action == 'list_preferences':
            if action.facts:
                return 'Case facts cannot become general memory. Please ask separately about saved preferences.'
            return await PreferenceInspection().run(turn)
        if action.action == 'preference':
            if action.facts:
                return 'Case facts cannot become general memory. Please clarify the preference to manage.'
            return await ConversationService(turn.owner_id, PreferenceRunner(self.model, usage, action.preference_intent)).run(turn.text)
        if action.action in ('conversation', 'memory'):
            if action.facts:
                return 'Case facts cannot become general memory. Please clarify the preference or conversation information to manage.'
            if action.action == 'conversation' and action.conversation_reply and intake.conversation is not None:
                if not await self.store.general_allowed(replace(turn, text=action.conversation_reply), epoch):
                    return 'Case material cannot become general conversation history. Please ask about the case directly.'
                return await DirectConversation().save(turn, action.conversation_reply, intake.conversation)
            return await self.memory(turn)
        if action.action == 'public':
            if action.facts:
                return 'Please send an independent public question without case facts.'
            with tracing.public_content():
                return await self.public_run(turn.text) if self.public_run else 'Public research is unavailable in this session.'
        if action.action == 'show' and action.requested_fact_keys and current:
            answer = '\n'.join(f'**{key.replace("_", " ").capitalize()}:** {current.facts.get(key, "Not recorded yet")}' for key in action.requested_fact_keys)
        else:
            answer = await self.store.apply(turn, action, selection, current)
        if action.action == 'show' and action.include_preferences:
            answer += '\n\n' + PreferenceInspection.render(await PreferenceStore().list(turn.owner_id))
        return answer

    async def button(self, turn: OwnerTurn, epoch: int) -> str:
        action = CaseAction.verify(turn.callback, turn.owner_id, epoch)
        selection = await self.store.selection(turn.owner_id, turn.conversation_id)
        if action is None or action.case_id != selection.case_id:
            return 'That button belongs to an older or different case. Please tell me what you’d like to do.'
        current = await self.store.get_case(turn.owner_id, action.case_id)
        if current is None:
            return 'That case is no longer available.'
        if action.kind == 'g':
            if self.correspondence.ensure_connected is not None:
                try:
                    await sync_to_async(self.correspondence.ensure_connected)()
                except GmailDisconnected:
                    raise GmailConnectionRequested('', True) from None
            await self.correspondence.accept(turn.owner_id)
            pending = await self.correspondence.pending_goal(turn)
            return await self._run(replace(turn, text=pending, callback=''), epoch) if pending else 'Gmail is ready. What would you like help with?'
        if action.kind == 'a':
            if self.correspondence.mailbox is None:
                return 'Please reconnect Gmail to continue.'
            request = replace(turn, text='Approve the displayed sending plan', callback='')
            return await FollowupService(self.correspondence.mailbox).control(request, selection, current,
                IntakeAction(action='gmail_followup_approve', intent_quote=request.text), timezone.now(),
                expected_generation=action.version)
        if action.kind == 'd':
            request = replace(turn, text='Draft a short reply for this case.', callback='')
            return await CaseAssistance(self.model, self.store, self.correspondence, self.plan).run(
                request, IntakeAction(action='case_help', case_id=current.id,
                    intent_quote=request.text, gmail_draft=True), selection, current, RunUsage())
        if action.kind == 'p':
            if self.correspondence.mailbox is None:
                return 'Please reconnect Gmail to continue.'
            request = replace(turn, text='Review sending plan for this draft.', callback='')
            return await FollowupService(self.correspondence.mailbox).control(request, selection, current,
                IntakeAction(action='gmail_followup_propose', intent_quote=request.text,
                    draft_version=action.version or None), timezone.now())
        return 'That action is unavailable.'

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
                 connect_gmail: Callable[[OwnerTurn, str, bool], Awaitable[ConnectionAttempt]] | None = None,
                 progress: Callable[[PreparedReply], Awaitable[None]] | None = None) -> None:
        self.connection_task: asyncio.Task[None] | None = None
        self.progress = progress
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
                    correspondence=Correspondence(GmailFactory.mailbox, GmailQueryPlanner(model), GmailFactory.require_connection), progress=self.progress)._execute(turn, epoch)
        except GmailConnectionRequested as request:
            if self.connect_gmail is None:
                return 'Gmail connection setup is available through the Telegram application on this Mac.'
            if self.progress is not None:
                if self.connection_task is not None and not self.connection_task.done():
                    return 'Gmail consent is already open on this Mac. Complete it there; I’ll continue automatically.'
                self.connection_task = asyncio.create_task(self._connect_in_background(replace(turn.followup(), callback=turn.callback), request, epoch))
                return 'I’m opening Gmail consent on this Mac. You can keep chatting while I wait; I’ll continue your case when access is ready.'
            return await self._connect_and_resume(turn, request, epoch)
        except PublicResearchRequested:
            if epoch != await CaseOwnership().epoch(turn.owner_id):
                return 'Case content was deleted. Please send a new public request.'
            with tracing.public_content():
                return await self.public_run(turn.text)

    async def _connect_and_resume(self, turn: OwnerTurn, request: GmailConnectionRequested, epoch: int) -> str:
        if self.connect_gmail is None:
            return 'Gmail connection is unavailable.'
        original_selection = await CaseStore().selection(turn.owner_id, turn.conversation_id)
        connected = await self.connect_gmail(turn, request.email, request.compose)
        if not connected.connected:
            return connected.message
        if epoch != await CaseOwnership().epoch(turn.owner_id):
            return connected.message + ' The case changed; please send a new request.'
        selection = await CaseStore().selection(turn.owner_id, turn.conversation_id)
        if selection.case_id != original_selection.case_id:
            return connected.message + ' Your selected case changed; tell me which case to continue.'
        if turn.callback and CaseAction.verify(turn.callback, turn.owner_id, epoch):
            await Correspondence.accept(turn.owner_id)
        pending = await Correspondence().pending_goal(turn)
        if pending and await Correspondence.consented(turn.owner_id):
            resumed = await self._execute(replace(turn.followup(), text=pending, callback=''), epoch)
            return resumed
        if pending:
            return connected.message + '\n\n' + CONSENT_MESSAGE
        return connected.message + ' Tell me which email or case you need help with.'

    async def _connect_in_background(self, turn: OwnerTurn, request: GmailConnectionRequested, epoch: int) -> None:
        async def complete(current: OwnerTurn, current_epoch: int) -> str:
            if current_epoch != epoch:
                return 'The case changed before Gmail connection started. Please send a new request.'
            try:
                return await self._connect_and_resume(current, request, epoch)
            except Exception as error:
                return user_error_message(error) + ' Please send your request again.'

        reply = await ApplicationTurn().prepare(turn, complete)
        if self.progress is not None:
            await self.progress(reply)

    async def finish_connections(self) -> None:
        if self.connection_task is not None:
            await self.connection_task

    async def close(self) -> None:
        if self.connection_task is not None:
            self.connection_task.cancel()
            await asyncio.gather(self.connection_task, return_exceptions=True)

    async def defer_public(self, message: str) -> str:
        raise PublicResearchRequested

"""Route private intake before any public conversation history or web-enabled agent."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
import json
import re

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.turns import OwnerTurn
from core.tracing import tracing
from grievances import constants
from grievances.prompts import INTAKE_INSTRUCTIONS, SELECTED_INTAKE_INSTRUCTIONS
from grievances.schema import IntakeAction
from grievances.store import CaseStore
from preferences.store import PreferenceStore
from subscriptions.service import SubscriptionService
from conversations.constants import DELETE_CONVERSATION_PHRASES
from conversations.service import ConversationService
from conversations.memory import ConversationMemoryRunner


class ApplicationService:
    def __init__(self, model: Model, public_run: Callable[[str], Awaitable[str]] | None = None) -> None:
        self.store = CaseStore()
        self.model = model
        self.public_run = public_run
        self.agent: Agent[None, IntakeAction] = Agent(model, output_type=IntakeAction,
            instructions=INTAKE_INSTRUCTIONS, name='case_intake', retries=constants.VALIDATION_RETRIES,
            model_settings={'max_tokens': constants.MAX_OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()

    async def run(self, turn: OwnerTurn) -> str:
        async with asyncio.timeout(constants.RUN_TIMEOUT_SECONDS):
            return await self._run(turn)

    async def _run(self, turn: OwnerTurn) -> str:
        if self.is_memory_control(turn.text):
            return await self.memory(turn)
        replay = await self.store.replay(turn)
        if replay:
            return replay
        if len(turn.text) > constants.MAX_INPUT_CHARS:
            return 'Please send a shorter case message. Nothing changed.'
        selection = await self.store.selection(turn.owner_id, turn.conversation_id)
        current = await self.store.get_case(turn.owner_id, selection.case_id) if selection.case_id else None
        cases = await self.store.list_cases(turn.owner_id)
        preferences = await PreferenceStore().list(turn.owner_id)
        payload = {'current_request': turn.text,
                   'selected_case': {'id': current.id} if current else None,
                   'case_labels': [asdict(case) for case in cases],
                   'saved_preferences': [{'key': p.key, 'value': p.value} for p in preferences]}
        encoded = json.dumps(payload)
        if len(encoded) > constants.MAX_CONTEXT_CHARS:
            return 'This case directory exceeds the intake context limit. Nothing changed.'
        usage = RunUsage()
        with tracing.operation('case.intake', {}, kind='agent', private=True):
            result = await self.agent.run(encoded, usage=usage, usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS),
                                          timeout=constants.RUN_TIMEOUT_SECONDS)
        action = result.output
        if action.action in ('show', 'update') and current:
            messages: list[str] = []
            size = len(json.dumps(current.facts)) + len(turn.text)
            for message in reversed(current.messages):
                if size + len(message) > constants.MAX_CONTEXT_CHARS // 2:
                    break
                messages.insert(0, message)
                size += len(message)
            selected_payload = json.dumps({'current_request': turn.text,
                'selected_case': {'id': current.id, 'facts': current.facts, 'messages': messages},
                'saved_preferences': [{'key': p.key, 'value': p.value} for p in preferences]})
            if len(selected_payload) > constants.MAX_CONTEXT_CHARS:
                return 'This case exceeds the intake context limit. Nothing changed.'
            with tracing.operation('case.selected_intake', {}, kind='agent', private=True):
                action = (await self.agent.run(selected_payload, instructions=SELECTED_INTAKE_INSTRUCTIONS, usage=usage,
                    usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS),
                    timeout=constants.RUN_TIMEOUT_SECONDS)).output
            if action.action not in ('show', 'update', 'clarify'):
                return 'Please clarify the intended case action. Nothing changed.'
        if any(f.quote not in turn.text or f.value not in f.quote for f in action.facts):
            return 'I could not verify the supplied facts. Nothing changed; please state them explicitly.'
        if action.action in ('create', 'select', 'leave') and (not action.intent_quote or action.intent_quote not in turn.text):
            return 'Please explicitly state the case action. Nothing changed.'
        if action.action == 'create' and (not action.label.strip() or action.label not in turn.text
                or len(' '.join(action.label.casefold().split())) > 120):
            return 'Please supply a short label for the new case. Nothing changed.'
        if action.correction_quote and (action.correction_quote not in turn.text or not re.search(
                r'\b(correct(?:ion)?|replace|change|update|amend)\b', action.correction_quote, re.IGNORECASE)):
            return 'Please explicitly state the correction. Nothing changed.'
        if action.action in ('show', 'update') and action.case_id and action.case_id != selection.case_id:
            return 'Please explicitly select the intended case first. Nothing changed.'
        if action.action == 'memory':
            if action.facts or await self.store.contains_private_material(turn.owner_id, turn.text):
                return 'Case facts cannot become general memory. Please clarify the preference or conversation information to manage.'
            return await self.memory(turn)
        if action.action == 'public':
            if current or action.facts or await self.store.contains_private_material(turn.owner_id, turn.text):
                return 'Please explicitly return to general research and send a public question separately, or select a case.'
            return await self.public_run(turn.text) if self.public_run else 'Public research is unavailable in this session.'
        return await self.store.apply(turn, action, selection, current)

    @staticmethod
    def is_memory_control(message: str) -> bool:
        control = message.strip().rstrip('.!').casefold()
        return control in DELETE_CONVERSATION_PHRASES or control == 'retry trace cleanup'

    async def memory(self, turn: OwnerTurn) -> str:
        with tracing.operation('application.memory', {}, kind='agent', private=True):
            return await ConversationService(turn.owner_id, ConversationMemoryRunner(self.model)).run(turn.text)


class PublicResearchRequested(Exception):
    """Unwind the private subscription session before entering public research."""


class SubscriptionApplicationService:
    """Open the subscription transport for one normalized application turn."""

    def __init__(self, subscription: SubscriptionService, public_run: Callable[[str], Awaitable[str]]) -> None:
        self.subscription = subscription
        self.public_run = public_run

    async def run(self, turn: OwnerTurn) -> str:
        if ApplicationService.is_memory_control(turn.text):
            with tracing.operation('application.memory', {}, kind='agent', private=True):
                return await self.public_run(turn.text)
        try:
            async with self.subscription.model() as model:
                return await ApplicationService(model, self.defer_public).run(turn)
        except PublicResearchRequested:
            return await self.public_run(turn.text)

    async def defer_public(self, message: str) -> str:
        raise PublicResearchRequested

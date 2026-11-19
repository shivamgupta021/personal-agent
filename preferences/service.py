"""Current-user consent and application-owned receipts for lasting preferences."""

import asyncio
from functools import partial
import json
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import UsageLimits

from conversations.budget import ConversationBudget
from conversations.facts import ConversationEditRejected, EditIntent, EditResult, FactCleanup, FactEditor
from preferences import constants
from preferences.prompts import PREFERENCE_AUTHORIZATION, PREFERENCE_CONTEXT
from preferences.store import Preference, PreferenceStore
from research.constants import MAX_MODEL_REQUESTS
from core.tracing import tracing


class PreferenceIntent(BaseModel):
    action: Literal['save', 'list', 'forget', 'clarify', 'none']
    key: str = Field(default='', max_length=constants.KEY_CHARS, pattern=r'^[a-z0-9_]*$')
    value: str = Field(default='', max_length=constants.VALUE_CHARS)
    consent_quote: str = Field(default='', max_length=constants.SOURCE_CHARS)
    value_quote: str = Field(default='', max_length=constants.VALUE_CHARS)
    suggestion_quote: str = Field(default='', max_length=constants.SOURCE_CHARS)
    question: str = Field(default='', max_length=constants.VALUE_CHARS)


class PreferenceReply(Exception):
    """End generation after a deterministic inspection or committed mutation receipt."""


class PreferenceService:
    def __init__(self, model: Model, facts: FactCleanup, *, private: bool = False) -> None:
        self.facts = facts
        self.store = PreferenceStore()
        self.editor = FactEditor(model, facts, private=private)
        self.current: list[Preference] = []
        options: ModelSettings = {'max_tokens': constants.AUTHORIZATION_OUTPUT_TOKENS}
        self.authorizer: Agent[None, PreferenceIntent] = Agent(model, output_type=PreferenceIntent,
            instructions=PREFERENCE_AUTHORIZATION, name='preference_authorization', retries=0,
            model_settings=options, capabilities=[ConversationBudget()])
        if private:
            self.authorizer.instrument = tracing.private_instrumentation()

    async def instructions(self) -> str:
        self.current = await self.store.list(self.facts.identity)
        return PREFERENCE_CONTEXT.format(preferences=json.dumps([{'key': p.key, 'value': p.value} for p in self.current]))

    def latest_assistant(self) -> str:
        for message in reversed(self.facts.context.messages):
            if isinstance(message, ModelResponse):
                return '\n'.join(part.content for part in message.parts if isinstance(part, TextPart))
        return ''

    async def manage_preferences(self, ctx: RunContext[None]) -> EditResult:
        """Save, inspect, update or forget a lasting preference only as the current user requests.

        Takes no model-supplied permission. Ambiguous consent needs clarification.
        """
        try:
            async with asyncio.timeout(constants.AUTHORIZATION_TIMEOUT_SECONDS):
                intent = (await self.authorizer.run(json.dumps({
                    'latest_user_request': self.facts.request,
                    'latest_assistant': self.latest_assistant(),
                    'current_preferences': [{'key': p.key, 'value': p.value} for p in self.current],
                }), usage=ctx.usage, usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS))).output
        except Exception as error:
            raise ConversationEditRejected('Preference authorization failed. Nothing changed; please try again.') from error
        if intent.action in ('none', 'clarify'):
            return EditResult('clarify', intent.question or 'Should this be a lasting saved preference, or only for this task?')
        if intent.action == 'list':
            text = '\n'.join(f'{p.key.replace("_", " ")}: {p.value}' for p in self.current)
            receipt = 'Saved preferences:\n' + text if text else 'No saved preferences.'
            await self.persist_reply(receipt)
            raise PreferenceReply(receipt)
        if not intent.key or not intent.consent_quote or intent.consent_quote not in self.facts.request:
            raise ConversationEditRejected('I could not verify the current request. Nothing changed; please state what to save or forget.')
        if intent.action == 'save':
            source = intent.suggestion_quote or self.facts.request
            if (not intent.value or intent.value != intent.value_quote or intent.value_quote not in source
                    or intent.suggestion_quote and intent.suggestion_quote not in self.latest_assistant()):
                raise ConversationEditRejected('I could not verify the proposed value. Nothing changed; please state the preference to save.')
            if len(self.facts.request) > constants.SOURCE_CHARS:
                raise ConversationEditRejected('Please send a shorter preference request. Nothing changed.')
            receipt = f'Saved preference: {intent.key.replace("_", " ")}: {intent.value}.'
            suggestion_id = ''
            if intent.suggestion_quote:
                for message in reversed(self.facts.context.messages):
                    if isinstance(message, ModelResponse):
                        suggestion_id = str((message.metadata or {}).get('archive_id', ''))
                        break
                if not suggestion_id:
                    raise ConversationEditRejected('The suggestion source is unavailable. Nothing changed; please state the preference.')
            await self.persist_reply(receipt, key=intent.key, value=intent.value, suggestion_id=suggestion_id)
            raise PreferenceReply(receipt)
        current = next((p for p in self.current if p.key == intent.key), None)
        scope = f'Saved preference {intent.key.replace("_", " ")} and all its historical values, suggestions, approvals and copies'
        if current:
            scope += ': ' + current.value
        try:
            async with asyncio.timeout(constants.AUTHORIZATION_TIMEOUT_SECONDS):
                plan = await self.editor.edit(ctx, EditIntent(action='forget', scope=scope))
        except Exception as error:
            raise ConversationEditRejected('I could not safely forget that preference. Nothing changed.') from error
        if isinstance(plan, EditResult):
            raise ConversationEditRejected(plan.message)
        await self.facts.apply(plan.messages, plan.intent, matched=current is not None or plan.matched,
                               mutation=partial(self.store.delete_in_transaction, self.facts.identity, intent.key))
        return EditResult('unchanged', 'Nothing changed.')

    async def persist_reply(self, receipt: str, *, key: str = '', value: str = '', suggestion_id: str = '') -> None:
        commit = asyncio.create_task(self.store.record_reply(self.facts.identity, self.facts.context,
            self.facts.request, receipt, key=key, value=value, suggestion_id=suggestion_id))
        try:
            await asyncio.shield(commit)
        except asyncio.CancelledError:
            await commit
            raise
        except ValueError as error:
            raise ConversationEditRejected(str(error) + ' Nothing changed.') from error

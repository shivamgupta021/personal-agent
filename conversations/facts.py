"""Bounded semantic selection with validated surgical edits and atomic persistence."""

import asyncio
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Annotated, Literal, Any
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequestPart, ModelResponsePart, ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, UserPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import UsageLimits

from conversations import constants
from conversations.budget import ConversationBudget
from conversations.context import ConversationContext
from conversations.prompts import FACT_EDIT_AUTHORIZATION, FACT_EDIT_REDACTION, FACT_EDIT_VERIFICATION
from conversations.store import ConversationStore
from research.constants import MAX_MODEL_REQUESTS


class EditIntent(BaseModel):
    action: Literal['forget', 'correct', 'clarify', 'none']
    scope: str = Field(max_length=constants.FACT_EDIT_SCOPE_CHARS)
    replacement: str = Field(default='', max_length=constants.FACT_EDIT_SCOPE_CHARS)
    question: str = Field(default='', max_length=constants.FACT_EDIT_SCOPE_CHARS)


class Redaction(BaseModel):
    segment_id: str
    quote: str = Field(min_length=1)


class Redactions(BaseModel):
    edits: list[Redaction]


class EditVerification(BaseModel):
    safe: bool
    reason: str = Field(default="", max_length=constants.FACT_EDIT_SCOPE_CHARS)


@dataclass
class EditResult:
    status: str
    message: str


@dataclass
class FactEditPlan:
    messages: list[ModelMessage]
    intent: EditIntent
    matched: bool


@dataclass
class EditableText:
    segment_id: str
    text: str
    source: str
    container: dict[str, Any] | list[Any]
    key: str | int
    dictionary_key: bool = False
    scalar: bool = False

    def replace(self, text: str) -> None:
        if self.scalar:
            if text:
                raise ValueError('Scalar redaction must remove the complete value')
            if isinstance(self.container, dict) and isinstance(self.key, str):
                self.container[self.key] = None
            elif isinstance(self.container, list) and isinstance(self.key, int):
                self.container[self.key] = None
            else:
                raise ValueError('Invalid scalar edit')
        elif self.dictionary_key:
            if not isinstance(self.container, dict) or not isinstance(self.key, str):
                raise ValueError('Invalid dictionary key edit')
            replacement = text or f'[redacted-key-{self.segment_id}]'
            if replacement != self.key and replacement in self.container:
                raise ValueError('Dictionary key collision')
            self.container[replacement] = self.container.pop(self.key)
        elif isinstance(self.container, dict) and isinstance(self.key, str):
            self.container[self.key] = text
        elif isinstance(self.container, list) and isinstance(self.key, int):
            self.container[self.key] = text
        else:
            raise ValueError('Invalid text edit')


class ConversationEditRejected(Exception):
    """An authorized edit failed before commit; do not retain its request as a fact."""


class ConversationEdited(Exception):
    def __init__(self, action: Literal['forget', 'correct'], matched: bool = True) -> None:
        self.action = action
        self.matched = matched
        super().__init__('Conversation content edited')


class FactCleanup:
    def __init__(self, identity: str, store: ConversationStore, request: str, context: ConversationContext) -> None:
        self.identity = identity
        self.store = store
        self.request = request
        self.context = context
        self.prior_messages = deepcopy(context.messages)

    async def originals(self) -> list[ModelMessage]:
        archive = await self.store.archive(self.identity)
        messages = ModelMessagesTypeAdapter.validate_python(list(archive.values())) if archive else self.prior_messages
        # Preserve original text and tool evidence. Discard provider wrappers,
        # reasoning and stale summaries, whose prose can duplicate forgotten facts.
        cleaned: list[ModelMessage] = []
        for message in messages:
            key = (message.metadata or {}).get('archive_id') or uuid4().hex
            if isinstance(message, ModelRequest):
                parts: list[ModelRequestPart] = []
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                        parts.append(UserPromptPart(part.content, timestamp=part.timestamp))
                    elif isinstance(part, ToolReturnPart):
                        parts.append(ToolReturnPart(part.tool_name, deepcopy(part.content), part.tool_call_id,
                                                    timestamp=part.timestamp, outcome=part.outcome))
                if parts:
                    cleaned.append(ModelRequest(parts=parts, metadata={'archive_id': key}))
            else:
                response_parts: list[ModelResponsePart] = []
                for response_part in message.parts:
                    if isinstance(response_part, TextPart):
                        response_parts.append(TextPart(response_part.content))
                    elif isinstance(response_part, ToolCallPart):
                        original_args = deepcopy(response_part.args)
                        arguments: str | dict[str, Any] | None = None
                        if isinstance(original_args, dict):
                            arguments = dict(original_args)
                        elif isinstance(original_args, str):
                            arguments = original_args
                            try:
                                parsed = json.loads(original_args)
                                if isinstance(parsed, dict):
                                    arguments = parsed
                            except ValueError:
                                pass
                        response_parts.append(ToolCallPart(response_part.tool_name, arguments, response_part.tool_call_id))
                if response_parts:
                    cleaned.append(ModelResponse(parts=response_parts, timestamp=message.timestamp,
                                                 metadata={'archive_id': key}))
        cleaned.sort(key=self.timestamp)
        cleaned.append(ModelRequest(parts=[UserPromptPart(self.request)],
                                    metadata={'archive_id': uuid4().hex, 'current_edit_request': True}))
        return cleaned

    @staticmethod
    def timestamp(message: ModelMessage) -> str:
        if isinstance(message, ModelResponse):
            return message.timestamp.isoformat()
        return min(p.timestamp.isoformat() for p in message.parts if isinstance(p, (UserPromptPart, ToolReturnPart)))

    @staticmethod
    def recent(messages: list[ModelMessage]) -> list[ModelMessage]:
        # Keep complete user exchanges so retained tool results keep their calls.
        exchanges: list[list[ModelMessage]] = []
        for message in messages:
            starts_turn = isinstance(message, ModelRequest) and any(isinstance(p, UserPromptPart) for p in message.parts)
            if not exchanges or starts_turn:
                exchanges.append([])
            exchanges[-1].append(message)
        history: list[ModelMessage] = []
        for exchange in reversed(exchanges):
            candidate = [*exchange, *history]
            if len(ModelMessagesTypeAdapter.dump_json(candidate)) > constants.FACT_EDIT_RECENT_BYTES:
                break
            history = candidate
        return history

    async def apply(self, messages: list[ModelMessage], intent: EditIntent, *, matched: bool = True, mutation: Callable[[], None] | None = None) -> None:
        messages = [message for message in messages if any(
            isinstance(part, (ToolCallPart, ToolReturnPart)) or
            (isinstance(part, (UserPromptPart, TextPart)) and part.content) for part in message.parts)]
        for message in messages:
            message.metadata = {'archive_id': (message.metadata or {})['archive_id']}
        if intent.action == 'correct':
            messages.append(ModelRequest(parts=[UserPromptPart(intent.replacement)], metadata={'archive_id': uuid4().hex}))
        commit = asyncio.create_task(self.store.replace_facts(self.identity, self.context, messages, self.recent(messages), mutation))
        try:
            await asyncio.shield(commit)
        except asyncio.CancelledError:
            # A sync-to-async database transaction cannot be cancelled once its
            # thread starts. Wait for the outcome before trace cleanup/finalization.
            await commit
            raise
        if intent.action in ('forget', 'correct'):
            raise ConversationEdited(intent.action, matched)


class FactEditor:
    def __init__(self, model: Model, cleanup: FactCleanup) -> None:
        self.cleanup = cleanup
        options: ModelSettings = {'max_tokens': constants.FACT_EDIT_OUTPUT_TOKENS}
        self.authorizer: Agent[None, EditIntent] = Agent(model, output_type=EditIntent,
            instructions=FACT_EDIT_AUTHORIZATION, name='fact_edit_authorization', retries=0,
            model_settings=options, capabilities=[ConversationBudget()])
        self.redactor: Agent[None, Redactions] = Agent(model, output_type=Redactions,
            instructions=FACT_EDIT_REDACTION, name='fact_edit_redaction', retries=0,
            model_settings=options, capabilities=[ConversationBudget()])
        self.verifier: Agent[None, EditVerification] = Agent(model, output_type=EditVerification,
            instructions=FACT_EDIT_VERIFICATION, name='fact_edit_verification', retries=0,
            model_settings=options, capabilities=[ConversationBudget()])

    async def edit_conversation_fact(self, ctx: RunContext[None], target: Annotated[str, Field(min_length=1, max_length=constants.FACT_EDIT_SCOPE_CHARS)]) -> EditResult:
        """Correct or forget a selected conversation fact only on an explicit current user request.

        Preserves unrelated prose, removes derived copies, and ends the turn after a
        successful atomic edit. Ambiguous targets require clarification.
        """
        intent: EditIntent | None = None
        try:
            async with asyncio.timeout(constants.FACT_EDIT_TIMEOUT_SECONDS):
                evidence = ModelMessagesTypeAdapter.dump_json(self.cleanup.context.messages).decode()
                intent = (await self.authorizer.run(json.dumps({'latest_user_request': self.cleanup.request,
                    'proposed_target': target, 'recent_context': evidence}), usage=ctx.usage,
                    usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS))).output
                plan = await self.edit(ctx, intent)
        except Exception:
            plan = EditResult('unchanged', 'I could not safely edit that information. Nothing was changed; please clarify the fact or try again.')
        if isinstance(plan, EditResult):
            if plan.status == 'unchanged':
                raise ConversationEditRejected(plan.message)
            return plan
        # Network/planning timeout ends before the durable transaction starts.
        await self.cleanup.apply(plan.messages, plan.intent, matched=plan.matched)
        return EditResult('unchanged', 'Nothing was changed.')

    @staticmethod
    def segments(raw: list[dict[str, Any]]) -> list[EditableText]:
        segments: list[EditableText] = []

        def walk(container: dict[str, Any] | list[Any], key: str | int, source: str) -> None:
            value = container[key] if isinstance(container, dict) and isinstance(key, str) else container[key] if isinstance(container, list) and isinstance(key, int) else None
            if isinstance(value, str):
                segments.append(EditableText(str(len(segments)), value, source, container, key))
            elif isinstance(value, (int, float, bool)):
                segments.append(EditableText(str(len(segments)), json.dumps(value), source, container, key, scalar=True))
            elif isinstance(value, dict):
                for child_key in list(value):
                    # Tool data keys can themselves contain identifiers. Preserve
                    # their values even when the key needs redaction.
                    walk(value, child_key, source)
                    segments.append(EditableText(str(len(segments)), child_key, source, value, child_key, True))
            elif isinstance(value, list):
                for child_index in range(len(value)):
                    walk(value, child_index, source)

        for message in raw:
            source = 'current_edit_request' if message.get('metadata', {}).get('current_edit_request') else 'original'
            for part in message['parts']:
                if part['part_kind'] in ('user-prompt', 'text', 'tool-return'):
                    walk(part, 'content', source)
                elif part['part_kind'] == 'tool-call':
                    walk(part, 'args', source)
        return segments

    async def edit(self, ctx: RunContext[None], intent: EditIntent) -> EditResult | FactEditPlan:
        limits = UsageLimits(request_limit=MAX_MODEL_REQUESTS)
        if intent.action in ('none', 'clarify'):
            return EditResult('clarify', intent.question or 'Which fact should I change or forget? Please state the intended scope.')
        if intent.action == 'correct' and (not intent.replacement or intent.replacement not in self.cleanup.request):
            return EditResult('unchanged', 'Please state the complete corrected fact, including its subject.')
        messages = await self.cleanup.originals()
        raw = ModelMessagesTypeAdapter.dump_python(messages, mode='json')
        locations = self.segments(raw)
        segments = [{'id': segment.segment_id, 'text': segment.text, 'source': segment.source, 'kind': 'scalar' if segment.scalar else 'text'} for segment in locations]
        batches: list[list[dict[str, str]]] = []
        current: list[dict[str, str]] = []
        for segment in segments:
            if len(json.dumps([segment])) > constants.FACT_EDIT_BATCH_CHARS:
                return EditResult('unchanged', 'An original message is too large to edit safely. Nothing was changed.')
            if len(json.dumps([*current, segment])) > constants.FACT_EDIT_BATCH_CHARS:
                batches.append(current)
                current = []
            current.append(segment)
        if current:
            batches.append(current)
        if len(batches) > constants.FACT_EDIT_MAX_BATCHES:
            return EditResult('unchanged', 'The archive is too large for a bounded edit. Nothing was changed.')
        planned: list[Redaction] = []
        originals = {s['id']: s['text'] for s in segments}
        for batch in batches:
            result = await self.redactor.run(json.dumps({'scope': intent.scope, 'action': intent.action,
                'latest_user_request': self.cleanup.request, 'authorized_replacement': intent.replacement, 'segments': batch}), usage=ctx.usage, usage_limits=limits)
            allowed = {s['id'] for s in batch}
            for edit in result.output.edits:
                if edit.segment_id not in allowed or originals[edit.segment_id].count(edit.quote) != 1:
                    return EditResult('unchanged', 'The edit could not be verified against the originals. Nothing was changed.')
                planned.append(edit)
            verified = await self.verifier.run(json.dumps({'scope': intent.scope, 'action': intent.action,
                'latest_user_request': self.cleanup.request, 'authorized_replacement': intent.replacement,
                'segments': batch, 'proposed_edits': [edit.model_dump() for edit in result.output.edits]}),
                usage=ctx.usage, usage_limits=limits)
            if not verified.output.safe:
                return EditResult('unchanged', 'I could not verify that only the selected information would be removed. Nothing was changed.')
        rewritten = dict(originals)
        for edit in planned:
            if rewritten[edit.segment_id].count(edit.quote) != 1:
                return EditResult('unchanged', 'Overlapping edits could not be applied safely. Nothing was changed.')
            rewritten[edit.segment_id] = rewritten[edit.segment_id].replace(edit.quote, '')
        for location in locations:
            updated = rewritten[location.segment_id]
            if updated != location.text:
                location.replace(updated)
        messages = ModelMessagesTypeAdapter.validate_python(raw)
        original_ids = {segment.segment_id for segment in locations if segment.source == 'original'}
        return FactEditPlan(messages, intent, any(edit.segment_id in original_ids for edit in planned))

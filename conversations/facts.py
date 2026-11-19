"""Bounded semantic selection with validated surgical edits and atomic persistence."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, UserPromptPart
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


@dataclass
class EditResult:
    status: str
    message: str


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
        # Tool payloads, provider details, reasoning, and old summaries are derived
        # copies. Rebuild only original human/assistant prose, stripped of wrappers.
        cleaned: list[ModelMessage] = []
        for message in messages:
            key = (message.metadata or {}).get('archive_id') or uuid4().hex
            if isinstance(message, ModelRequest):
                parts = [UserPromptPart(p.content, timestamp=p.timestamp) for p in message.parts
                         if isinstance(p, UserPromptPart) and isinstance(p.content, str)]
                if parts:
                    cleaned.append(ModelRequest(parts=parts, metadata={'archive_id': key}))
            else:
                text = [TextPart(p.content) for p in message.parts if isinstance(p, TextPart)]
                if text:
                    cleaned.append(ModelResponse(parts=text, timestamp=message.timestamp, metadata={'archive_id': key}))
        cleaned.sort(key=self.timestamp)
        return cleaned

    @staticmethod
    def timestamp(message: ModelMessage) -> str:
        if isinstance(message, ModelResponse):
            return message.timestamp.isoformat()
        return next(p.timestamp.isoformat() for p in message.parts if isinstance(p, UserPromptPart))

    async def apply(self, messages: list[ModelMessage], intent: EditIntent, *, matched: bool = True) -> None:
        messages = [message for message in messages if any(
            isinstance(part, (UserPromptPart, TextPart)) and part.content for part in message.parts)]
        if intent.action == 'correct':
            messages.append(ModelRequest(parts=[UserPromptPart(intent.replacement)], metadata={'archive_id': uuid4().hex}))
        history: list[ModelMessage] = []
        for message in reversed(messages):
            candidate = [message, *history]
            if len(ModelMessagesTypeAdapter.dump_json(candidate)) > constants.FACT_EDIT_RECENT_BYTES:
                break
            history = candidate
        await self.store.replace_facts(self.identity, self.context, messages, history)
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
        try:
            async with asyncio.timeout(constants.FACT_EDIT_TIMEOUT_SECONDS):
                return await self.edit(ctx, target)
        except ConversationEdited:
            raise
        except Exception:
            return EditResult('unchanged', 'I could not safely edit that information. Nothing was changed; please clarify the fact or try again.')

    async def edit(self, ctx: RunContext[None], target: str) -> EditResult:
        limits = UsageLimits(request_limit=MAX_MODEL_REQUESTS)
        evidence = ModelMessagesTypeAdapter.dump_json(self.cleanup.context.messages).decode()
        intent = (await self.authorizer.run(json.dumps({'latest_user_request': self.cleanup.request,
            'proposed_target': target, 'recent_context': evidence}), usage=ctx.usage, usage_limits=limits)).output
        if intent.action in ('none', 'clarify'):
            return EditResult('clarify', intent.question or 'Which fact should I change or forget? Please state the intended scope.')
        if intent.action == 'correct' and (not intent.replacement or intent.replacement not in self.cleanup.request):
            return EditResult('unchanged', 'Please state the complete corrected fact, including its subject.')
        messages = await self.cleanup.originals()
        segments = []
        for index, message in enumerate(messages):
            for part_index, part in enumerate(message.parts):
                if isinstance(part, (UserPromptPart, TextPart)) and isinstance(part.content, str):
                    segments.append({'id': f'{index}:{part_index}', 'text': part.content})
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
                'latest_user_request': self.cleanup.request, 'segments': batch}), usage=ctx.usage, usage_limits=limits)
            allowed = {s['id'] for s in batch}
            for edit in result.output.edits:
                if edit.segment_id not in allowed or originals[edit.segment_id].count(edit.quote) != 1:
                    return EditResult('unchanged', 'The edit could not be verified against the originals. Nothing was changed.')
                planned.append(edit)
            verified = await self.verifier.run(json.dumps({'scope': intent.scope, 'action': intent.action,
                'segments': batch, 'proposed_edits': [edit.model_dump() for edit in result.output.edits]}),
                usage=ctx.usage, usage_limits=limits)
            if not verified.output.safe:
                return EditResult('unchanged', 'I could not verify that only the selected information would be removed. Nothing was changed.')
        rewritten = dict(originals)
        for edit in planned:
            if rewritten[edit.segment_id].count(edit.quote) != 1:
                return EditResult('unchanged', 'Overlapping edits could not be applied safely. Nothing was changed.')
            rewritten[edit.segment_id] = rewritten[edit.segment_id].replace(edit.quote, '')
        for key, text in rewritten.items():
            index, part_index = map(int, key.split(':'))
            part = messages[index].parts[part_index]
            if isinstance(part, (UserPromptPart, TextPart)):
                part.content = text.strip()
        await self.cleanup.apply(messages, intent, matched=bool(planned))
        return EditResult('unchanged', 'Nothing was changed.')

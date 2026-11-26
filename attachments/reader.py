"""Attachment answers and source persistence at the authenticated turn boundary."""
import asyncio
from dataclasses import replace
import json

from asgiref.sync import sync_to_async
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from attachments.constants import MAX_FILES, MAX_TEXT_CHARS, MAX_OUTPUT_TOKENS, READER_RETRIES, READER_REQUEST_LIMIT, EVIDENCE_HEADER_CHARS
from attachments.prompts import READ_INSTRUCTIONS
from attachments.service import AttachmentExtractor
from conversations.direct import DirectConversation
from conversations.store import ConversationStore
from core.tracing import tracing
from core.workers import drain_on_cancel
from core.turns import OwnerTurn
from grievances.models import CaseMessage
from grievances.mutations import CaseMutation
from grievances.store import CaseStore, CaseView, SelectionView
from research.constants import RUN_TIMEOUT_SECONDS


class AttachmentReader:
    def __init__(self, model: Model) -> None:
        self.agent = Agent(model, instructions=READ_INSTRUCTIONS, name='attachment_reader',
            model_settings={'max_tokens': MAX_OUTPUT_TOKENS}, retries=READER_RETRIES)
        self.agent.instrument = tracing.private_instrumentation()

    async def run(self, turn: OwnerTurn, epoch: int) -> str:
        store = CaseStore()
        selection = replace(await store.selection(turn.owner_id, turn.conversation_id), deletion_epoch=epoch)
        current = await store.get_case(turn.owner_id, selection.case_id) if selection.case_id else None
        context = await ConversationStore().context(turn.owner_id) if current is None else None
        extracted = [await drain_on_cancel(asyncio.to_thread(AttachmentExtractor().extract, file.data, file.name, file.mime, file.source))
                     for file in turn.uploads[:MAX_FILES]]
        evidence = [item.evidence() for item in extracted]
        if current and evidence:
            if not await self.save_case(turn, selection, current, evidence):
                return 'The selected case changed. Please upload the file again.'
        if not evidence and current:
            evidence = [row.text async for row in CaseMessage.objects.filter(case_id=current.id,
                case__owner_id=turn.owner_id, role='attachment').order_by('-pk')[:MAX_FILES]]
        if not evidence:
            return 'Please upload a PDF, PNG, JPEG or text file to read.'
        payload = json.dumps({'owner_request': turn.text, 'attachment_evidence': evidence, 'associated_case': current.label if current else None})
        if extracted and not any(item.text for item in extracted):
            answer = '\n'.join(f'{item.name}: {item.notice}' for item in extracted)
        else:
            answer = (await self.agent.run(payload, usage_limits=UsageLimits(request_limit=READER_REQUEST_LIMIT),
                timeout=RUN_TIMEOUT_SECONDS)).output
        if current:
            return answer
        if context is None:
            return answer
        return await DirectConversation().save(replace(turn, text=payload), answer, context)

    @staticmethod
    @sync_to_async
    def save_case(turn: OwnerTurn, selection: SelectionView, current: CaseView, evidence: list[str]) -> bool:
        with CaseMutation(turn, selection, current, operation='draft') as mutation:
            if mutation.receipt:
                return True
            if mutation.failure or mutation.case is None:
                return False
            for text in evidence:
                CaseMessage.objects.create(case=mutation.case, role='attachment', text=text[:MAX_TEXT_CHARS + EVIDENCE_HEADER_CHARS])
            mutation.record('attachment', mutation.case)
            return True

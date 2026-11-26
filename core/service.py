"""Main orchestrator: select a specialist and return its completed reply."""
from collections.abc import Awaitable, Callable

from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from attachments.reader import AttachmentReader
from conversations.constants import DELETE_CONVERSATION_PHRASES
from conversations.memory import ConversationMemoryRunner
from conversations.service import ConversationService
from core.routing import IntentRouter
from core.tracing import tracing
from core.turns import OwnerTurn, PreparedReply
from gmail.correspondence import Correspondence
from grievances.cleanup import CaseTraceCleanup
from core.lifecycle import ApplicationTurn
from grievances.service import CaseService
from grievances.store import CaseStore
from preferences.runner import PreferenceRunner
from research.tools.firecrawl import Firecrawl


class ApplicationService:
    def __init__(self, model: Model, public_run: Callable[[str], Awaitable[str]] | None = None, *,
                 firecrawl: Firecrawl | None = None, trace_cleanup: CaseTraceCleanup | None = None,
                 correspondence: Correspondence | None = None,
                 progress: Callable[[PreparedReply], Awaitable[None]] | None = None) -> None:
        self.model = model
        self.public_run = public_run
        self.router = IntentRouter(model)
        self.cases = CaseService(model, firecrawl=firecrawl, trace_cleanup=trace_cleanup,
            correspondence=correspondence, progress=progress)
        self.turns = ApplicationTurn(trace_cleanup)

    async def run(self, turn: OwnerTurn) -> str:
        return await (await self.prepare(turn)).render()

    async def prepare(self, turn: OwnerTurn) -> PreparedReply:
        return await self.turns.prepare(turn, self._execute)

    async def _execute(self, turn: OwnerTurn, epoch: int) -> str:
        # Buttons already identify a validated action. Never ask a model to reinterpret approval.
        if turn.callback:
            return await self.cases.button(turn, epoch)
        if turn.uploads:
            return await AttachmentReader(self.model).run(turn, epoch)
        if self.is_memory_control(turn.text):
            return await self.conversation(turn)
        if turn.text.strip().rstrip('.!').casefold() == 'i agree to email processing':
            return await self.cases.execute(turn, epoch)
        replay = await self.cases.store.replay(turn)
        if replay:
            return replay
        route = await self.router.route(turn)
        if route.specialist == 'grievance':
            return await self.cases.execute(turn, epoch)
        if route.specialist == 'attachments':
            return await AttachmentReader(self.model).run(turn, epoch)
        # A model's routing decision cannot authorize leaking case material.
        if not await CaseStore().general_allowed(turn, epoch):
            return 'That request includes case details. Please keep it within the case.'
        if route.specialist == 'preferences':
            return await ConversationService(turn.owner_id, PreferenceRunner(self.model, RunUsage())).run(turn.text)
        if route.specialist == 'research':
            with tracing.public_content():
                return await self.public_run(turn.text) if self.public_run else 'Public research is unavailable in this session.'
        return await self.conversation(turn)

    async def conversation(self, turn: OwnerTurn) -> str:
        return await ConversationService(turn.owner_id, ConversationMemoryRunner(self.model)).run(turn.text)

    @staticmethod
    def is_memory_control(message: str) -> bool:
        control = message.strip().rstrip('.!').casefold()
        return control in DELETE_CONVERSATION_PHRASES or control == 'retry trace cleanup'

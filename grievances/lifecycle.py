"""Own one delivery's authorization, execution and trace-producer lifetime."""

from collections.abc import Awaitable, Callable

from core.tracing import tracing
from core.email_telemetry import email_private
from gmail.correspondence import Correspondence
from core.turns import OwnerTurn, PreparedReply
from grievances.cleanup import CaseTraceCleanup
from grievances.deletion import CaseDeletionService
from grievances.ownership import CaseOwnership
from grievances.trace_lifecycle import ApplicationTrace


class ApplicationTurn:
    def __init__(self, trace_cleanup: CaseTraceCleanup | None = None) -> None:
        self.trace_cleanup = trace_cleanup
        self.deletion = CaseDeletionService(trace_cleanup)
        self.ownership = CaseOwnership()

    async def prepare(self, turn: OwnerTurn,
                      execute: Callable[[OwnerTurn, int], Awaitable[str]]) -> PreparedReply:
        consenting = turn.text.strip().rstrip('.!').casefold() == 'i agree to email processing'
        token = email_private.set(consenting or await Correspondence.consented(turn.owner_id))
        try:
            return await self._prepare(turn, execute)
        finally:
            email_private.reset(token)

    async def _prepare(self, turn: OwnerTurn,
                       execute: Callable[[OwnerTurn, int], Awaitable[str]]) -> PreparedReply:
        capture = ApplicationTrace(turn, self.trace_cleanup)
        async with capture.run() as span:
            epoch = await self.ownership.begin(turn)
            if epoch != await self.ownership.epoch(turn.owner_id):
                text = 'This delivery predates case deletion; nothing was recreated. Please send a new request.'
            else:
                tracing.record_input(span, {'message': turn.text})
                control = await self.deletion.control(turn)
                if control is None:
                    text = await execute(turn, epoch)
                else:
                    text = control.message
                    if control.deleted:
                        epoch = await self.ownership.epoch(turn.owner_id)

            async def valid() -> bool:
                return epoch == await self.ownership.epoch(turn.owner_id)

            reply = PreparedReply(text, valid)
            tracing.record_output(span, {'answer': await reply.render()})
            return reply

"""Own one delivery's authorization, execution and trace-producer lifetime."""

from collections.abc import Awaitable, Callable

from opentelemetry.trace import Status, StatusCode

from core.errors import user_error_message
from core.tracing import tracing
from core.capabilities import CapabilityHelp
from core.email_telemetry import email_private
from gmail.correspondence import Correspondence
from core.turns import OwnerTurn, PreparedReply
from preferences.inspection import PreferenceInspection
from grievances.cleanup import CaseTraceCleanup
from grievances.deletion import CaseDeletionService
from grievances.ownership import CaseOwnership
from grievances.store import CaseStore
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
                tracing.record_input(span, {'message': turn.text, **({'attachments': [{'name': item.name, 'mime': item.mime, 'bytes': len(item.data)} for item in turn.uploads]} if turn.uploads else {})})
                control = await self.deletion.control(turn)
                if control is None:
                    if not turn.uploads and PreferenceInspection.matches(turn.text) and await CaseStore().general_allowed(turn, epoch):
                        text = await PreferenceInspection().run(turn)
                    elif not turn.uploads and CapabilityHelp.matches(turn.text) and await CaseStore().general_allowed(turn, epoch):
                        text = await CapabilityHelp().run(turn)
                    else:
                        try:
                            text = await execute(turn, epoch)
                        except TimeoutError as error:
                            span.set_status(Status(StatusCode.ERROR, 'Request timed out'))
                            text = user_error_message(error)
                else:
                    text = control.message
                    if control.deleted:
                        epoch = await self.ownership.epoch(turn.owner_id)

            async def valid() -> bool:
                return epoch == await self.ownership.epoch(turn.owner_id)

            context = span.get_span_context()
            reply = PreparedReply(text, valid, format(context.trace_id, '032x') if context.is_valid else '')
            tracing.record_output(span, {'answer': await reply.render()})
            return reply

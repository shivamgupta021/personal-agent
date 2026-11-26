"""Present the real case workflow before accessing external evidence."""
from collections.abc import Awaitable, Callable

from opentelemetry.trace import get_current_span

from core.turns import OwnerTurn, PreparedReply
from grievances.ownership import CaseOwnership
from grievances.store import CaseView


class CasePlan:
    def __init__(self, deliver: Callable[[PreparedReply], Awaitable[None]] | None = None) -> None:
        self.deliver = deliver

    @staticmethod
    def text(case: CaseView) -> str:
        return (f'**Plan for {case.label}**\n'
            '1. Check your emails and receipts.\n'
            '2. Prepare a short refund request for your review.\n'
            '3. Send and follow up only after your approval.')

    async def present(self, turn: OwnerTurn, case: CaseView, epoch: int) -> str:
        text = self.text(case)
        if self.deliver is None:
            return text

        async def valid() -> bool:
            return epoch == await CaseOwnership().epoch(turn.owner_id)

        context = get_current_span().get_span_context()
        await self.deliver(PreparedReply(text, valid, format(context.trace_id, '032x') if context.is_valid else ''))
        return ''

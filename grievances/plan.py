"""Present the real case workflow before accessing external evidence."""
from collections.abc import Awaitable, Callable

from opentelemetry.trace import get_current_span

from core.turns import OwnerTurn, PreparedReply
from grievances.ownership import CaseOwnership
from grievances.store import CaseView


class CasePlan:
    def __init__(self, deliver: Callable[[PreparedReply], Awaitable[None]] | None = None) -> None:
        self.deliver = deliver

    async def present(self, turn: OwnerTurn, case: CaseView, epoch: int) -> str:
        remedy = case.facts.get('requested_remedy', 'a suitable resolution')
        text = (f'Here’s the plan for {case.label}:\n'
            f'1. Review your details and relevant emails to understand the request for {remedy}.\n'
            '2. Identify what is missing and propose the next step.\n'
            '3. Prepare a short Gmail draft when you ask, then get your approval before sending or scheduling follow-ups.\n'
            'I’ll ask for Gmail access if needed and pause when a reply needs your decision.')
        if self.deliver is None:
            return text

        async def valid() -> bool:
            return epoch == await CaseOwnership().epoch(turn.owner_id)

        context = get_current_span().get_span_context()
        await self.deliver(PreparedReply(text, valid, format(context.trace_id, '032x') if context.is_valid else ''))
        return ''

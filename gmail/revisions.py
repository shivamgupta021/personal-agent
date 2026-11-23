"""Resolve and revalidate the exact Gmail draft selected for a new revision."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from email.utils import getaddresses

from asgiref.sync import sync_to_async

from core.tracing import tracing
from core.turns import OwnerTurn
from gmail.errors import GmailError
from gmail.mailbox import Mailbox
from gmail.models import GmailDraftExport
from gmail.records import DraftEnvelope
from grievances.mutations import CaseMutation
from grievances.store import CaseView, SelectionView


@dataclass(frozen=True)
class RevisionSource:
    export_id: int
    gmail_id: str
    version: int
    envelope: DraftEnvelope


class DraftRevisions:
    def __init__(self, mailbox: Callable[[], AbstractContextManager[Mailbox]]) -> None:
        self.mailbox = mailbox

    @sync_to_async
    def resolve(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                mailbox: str, thread_id: str, version: int | None) -> GmailDraftExport:
        with CaseMutation(turn, selection, current, operation='read') as mutation:
            if mutation.failure:
                raise GmailError('The case changed; no draft was created.')
            rows = GmailDraftExport.objects.filter(draft__case_id=current.id,
                draft__case__owner_id=turn.owner_id, status='created').select_related('draft')
            if version is not None:
                rows = rows.filter(draft__version=version)
            choices = list(rows.order_by('-draft__version')[:2])
            if not choices:
                raise GmailError('I could not find that saved Gmail draft version. Which draft would you like revised?')
            if len(choices) > 1:
                raise GmailError(f'The latest Gmail draft is v{choices[0].draft.version}; earlier drafts will stay unchanged. '
                    'Which version should I revise?')
            row = choices[0]
            if row.mailbox.casefold() != mailbox.casefold() or row.thread_id != thread_id:
                raise GmailError('That draft belongs to a different mailbox or email conversation. Select its case correspondence before revising it.')
            return row

    async def read(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                   mailbox: str, thread_id: str, version: int | None) -> RevisionSource:
        row = await self.resolve(turn, selection, current, mailbox, thread_id, version)
        with self.mailbox() as client, tracing.operation('gmail.read_revision', {}, private=True):
            envelope = await sync_to_async(client.read_draft)(row.gmail_id, mailbox)
        if envelope.thread_id != row.thread_id or self.address(envelope.recipient) != self.address(row.recipient):
            raise GmailError('The Gmail draft recipient or conversation changed. Please confirm the intended recipient and draft before revising it.')
        return RevisionSource(row.pk, row.gmail_id, row.draft.version, envelope)

    @staticmethod
    def address(value: str) -> str:
        addresses = [address.casefold() for _, address in getaddresses([value])]
        if len(addresses) != 1 or not addresses[0]:
            raise GmailError('The draft must have exactly one recipient before revision.')
        return addresses[0]

    async def validate(self, source: RevisionSource) -> None:
        with self.mailbox() as client, tracing.operation('gmail.check_revision', {}, private=True):
            current = await sync_to_async(client.read_draft)(source.gmail_id, source.envelope.mailbox)
        if current != source.envelope:
            raise GmailError('The original Gmail draft changed while I was preparing the revision. No new draft was created; please request the revision again.')

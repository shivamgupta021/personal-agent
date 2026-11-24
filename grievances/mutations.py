"""Atomic ownership, stale-state and delivery checks shared by case writers.

The operation determines replay precedence: progress accepts a committed delivery
against an existing case, policy validates its snapshot first, and case/draft
writes replay before validating their snapshot. Domain-specific evidence checks
remain with their writers. No external I/O belongs inside this context.
"""

from types import TracebackType
from typing import Literal, TYPE_CHECKING
from uuid import UUID

from django.db import transaction

from core.turns import OwnerTurn
from grievances.models import CaseOperationReceipt, CaseSelection, GrievanceCase
from grievances.ownership import CaseOwnership

if TYPE_CHECKING:
    from grievances.store import CaseView, SelectionView


class CaseMutation:
    def __init__(self, turn: OwnerTurn, selection: 'SelectionView', current: 'CaseView | None', *,
                 operation: Literal['apply', 'draft', 'read', 'policy', 'progress'], target: str | None = None) -> None:
        self.turn = turn
        self.snapshot = selection
        self.current = current
        self.operation = operation
        self.target = target
        self.failure: Literal['deleted', 'selection', 'facts', 'progress'] | None = None
        self.receipt: CaseOperationReceipt | None = None
        self.case: GrievanceCase | None = None
        self.selected: CaseSelection | None = None
        self._transaction = transaction.atomic()

    def __enter__(self) -> 'CaseMutation':
        self._transaction.__enter__()
        try:
            self._check()
        except BaseException as error:
            self._transaction.__exit__(type(error), error, error.__traceback__)
            raise
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self._transaction.__exit__(kind, error, traceback)

    def _check(self) -> None:
        owner = CaseOwnership.lock(self.turn.owner_id)
        if owner.deletion_epoch != self.snapshot.deletion_epoch:
            self.failure = 'deleted'
            return
        self.selected = CaseSelection.objects.select_for_update().get(
            owner_id=self.turn.owner_id, conversation_id=self.turn.conversation_id)
        if self.operation in ('apply', 'draft'):
            self.receipt = self._receipt()
            if self.receipt is not None:
                return
        if self.operation == 'apply' and self.selected.revision != self.snapshot.revision:
            self.failure = 'selection'
            return
        target = self.target if self.operation == 'apply' else self.current.id if self.current else None
        if target:
            try:
                self.case = GrievanceCase.objects.select_for_update().filter(
                    owner_id=self.turn.owner_id, pk=UUID(target)).first()
            except ValueError:
                self.case = None
        if self.operation == 'apply':
            return  # Creation/selection may deliberately target another case.
        if self.operation == 'progress' and self.case is not None:
            self.receipt = self._receipt()
            if self.receipt is not None:
                return
        if (self.current is None or self.selected.revision != self.snapshot.revision
                or str(self.selected.case_id) != self.current.id):
            self.failure = 'selection'
        elif self.case is None or self.case.revision != self.current.revision:
            self.failure = 'facts'
        elif self.operation == 'progress' and self.case.progress_revision != self.current.progress_revision:
            self.failure = 'progress'
        elif self.operation == 'policy':
            self.receipt = self._receipt()

    def _receipt(self) -> CaseOperationReceipt | None:
        return CaseOperationReceipt.objects.filter(owner_id=self.turn.owner_id,
            conversation_id=self.turn.conversation_id, delivery_id=self.turn.delivery_id).first()

    def record(self, action: str, case: GrievanceCase | None, *, outcome: str = 'saved') -> CaseOperationReceipt:
        """Persist the delivery receipt in the same transaction as its changes."""
        if self.failure is not None or self.receipt is not None:
            raise RuntimeError('Cannot record an invalid or already committed mutation')
        self.receipt = CaseOperationReceipt.objects.create(owner_id=self.turn.owner_id,
            conversation_id=self.turn.conversation_id, delivery_id=self.turn.delivery_id,
            case_id=case.pk if case else None, revision=case.revision if case else 0, action=action, outcome=outcome)
        return self.receipt

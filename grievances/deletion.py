"""Erase exactly one owned case, retaining only content-free replay protection."""

from dataclasses import dataclass, replace
from uuid import UUID

import httpx2

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import F

from core.turns import OwnerTurn
from grievances.models import CaseOperationReceipt, CaseSelection, CaseTrace, GrievanceCase
from grievances.ownership import CaseOwnership
from grievances.cleanup import CaseTraceCleanup


@dataclass(frozen=True)
class DeletionResult:
    message: str
    deleted: bool = False


class DeletionCommand:
    @staticmethod
    def normalize(text: str) -> str:
        return text.strip().rstrip('.!').casefold()

    @classmethod
    def matches(cls, text: str, case: GrievanceCase, selected: bool) -> bool:
        commands = {f'delete case {case.label.casefold()}', f'delete the {case.label.casefold()} case',
                    f'delete case {case.pk}'}
        if selected:
            commands.update(('delete this case', 'delete the selected case', 'delete my selected case'))
        return cls.normalize(text) in commands


class CaseDeletionService:
    def __init__(self, cleanup: CaseTraceCleanup | None = None) -> None:
        self.cleanup = cleanup

    async def delete(self, turn: OwnerTurn, case_id: str) -> DeletionResult:
        result = await self._delete_local(turn, case_id)
        if result.deleted and await CaseTrace.objects.filter(owner_id=turn.owner_id, pending=True).aexists():
            try:
                status = await self.cleanup.run(turn.owner_id) if self.cleanup else await CaseTraceCleanup.configured(turn.owner_id)
            except (httpx2.HTTPError, ValueError):
                status = 'Trace cleanup pending. Say “retry case trace cleanup” later.'
            return replace(result, message=result.message + '\n' + status)
        return result

    async def control(self, turn: OwnerTurn) -> DeletionResult | None:
        command = DeletionCommand.normalize(turn.text)
        if command == 'retry case trace cleanup':
            status = await self.cleanup.run(turn.owner_id) if self.cleanup else await CaseTraceCleanup.configured(turn.owner_id)
            return DeletionResult(status)
        if not command.startswith('delete '):
            return None
        cases = [case async for case in GrievanceCase.objects.filter(owner_id=turn.owner_id)]
        selection = await CaseSelection.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id).afirst()
        for case in cases:
            selected = bool(selection and selection.case_id == case.pk)
            if DeletionCommand.matches(turn.text, case, selected):
                return await self.delete(turn, str(case.pk))
        if 'case' in command or command == 'delete it':
            return DeletionResult('Please identify the case to delete: “Delete this case” or “Delete case LABEL”. Nothing changed.')
        return None

    @sync_to_async
    def _delete_local(self, turn: OwnerTurn, case_id: str) -> DeletionResult:
        try:
            identifier = UUID(case_id)
        except ValueError:
            return DeletionResult('Please explicitly identify an owned case to delete.')
        with transaction.atomic():
            owner = CaseOwnership.lock(turn.owner_id)
            receipt = CaseOperationReceipt.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
                                                         delivery_id=turn.delivery_id).first()
            if receipt:
                return DeletionResult('This delivery was already processed; nothing was recreated.')
            case = GrievanceCase.objects.filter(owner_id=turn.owner_id, pk=identifier).first()
            if case is None:
                return DeletionResult('That case is unavailable; nothing was changed.')
            selected = CaseSelection.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
                                                     case=case).exists()
            if not DeletionCommand.matches(turn.text, case, selected):
                return DeletionResult('To delete, explicitly say “Delete this case” for the selected case or “Delete case LABEL”. Nothing changed.')
            CaseSelection.objects.filter(owner_id=turn.owner_id, case=case).update(case=None, revision=F('revision') + 1)
            CaseTrace.objects.filter(owner_id=turn.owner_id, case_id=case.pk).update(pending=True, confirmed=False)
            CaseOperationReceipt.objects.create(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
                delivery_id=turn.delivery_id, action='delete', case_id=case.pk, revision=case.revision, outcome='deleted')
            case.delete()
            owner.deletion_epoch += 1
            owner.save(update_fields=['deletion_epoch'])
        return DeletionResult('Case deleted locally. '
                'Other cases and independent preferences are preserved. Telegram, model-provider and independent backup copies are outside this deletion.', deleted=True)

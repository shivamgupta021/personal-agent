"""A short transaction lock shared by every case writer; never held over I/O."""

from asgiref.sync import sync_to_async

from django.db import transaction

from core.turns import OwnerTurn
from grievances.models import CaseOwnerState, CaseTurnRegistration


class CaseOwnership:
    @staticmethod
    def lock(owner_id: str) -> CaseOwnerState:
        CaseOwnerState.objects.get_or_create(owner_id=owner_id)
        return CaseOwnerState.objects.select_for_update().get(owner_id=owner_id)

    @sync_to_async
    def epoch(self, owner_id: str) -> int:
        row, _ = CaseOwnerState.objects.get_or_create(owner_id=owner_id)
        return row.deletion_epoch

    @sync_to_async
    def begin(self, turn: OwnerTurn) -> int:
        with transaction.atomic():
            owner = self.lock(turn.owner_id)
            row, _ = CaseTurnRegistration.objects.get_or_create(owner_id=turn.owner_id,
                conversation_id=turn.conversation_id, delivery_id=turn.delivery_id,
                defaults={'deletion_epoch': owner.deletion_epoch})
            return row.deletion_epoch

    @sync_to_async
    def followup(self, turn: OwnerTurn) -> OwnerTurn:
        child = turn.followup()
        with transaction.atomic():
            owner = self.lock(turn.owner_id)
            parent = CaseTurnRegistration.objects.filter(owner_id=turn.owner_id,
                conversation_id=turn.conversation_id, delivery_id=turn.delivery_id).first()
            CaseTurnRegistration.objects.get_or_create(owner_id=child.owner_id,
                conversation_id=child.conversation_id, delivery_id=child.delivery_id,
                defaults={'deletion_epoch': parent.deletion_epoch if parent else owner.deletion_epoch})
        return child

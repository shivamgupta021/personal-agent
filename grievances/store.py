"""Owner-scoped projections and short, revision-checked atomic case mutations."""

from dataclasses import dataclass
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import transaction

from core.turns import OwnerTurn
from grievances.constants import MATERIAL_FIELDS
from grievances.models import CaseFactRevision, CaseMessage, CaseOperationReceipt, CaseSelection, GrievanceCase
from grievances.schema import IntakeAction


@dataclass(frozen=True)
class CaseSummary:
    id: str
    label: str


@dataclass(frozen=True)
class SelectionView:
    case_id: str | None
    revision: int


@dataclass(frozen=True)
class CaseView:
    id: str
    label: str
    status: str
    revision: int
    facts: dict[str, str]
    history: list[dict[str, str]]
    messages: list[str]


class CaseStore:
    @sync_to_async
    def list_cases(self, owner_id: str) -> list[CaseSummary]:
        return [CaseSummary(str(row.pk), row.label) for row in GrievanceCase.objects.filter(owner_id=owner_id).order_by('created_at')]

    @sync_to_async
    def selection(self, owner_id: str, conversation_id: str) -> SelectionView:
        row, _ = CaseSelection.objects.get_or_create(owner_id=owner_id, conversation_id=conversation_id)
        return SelectionView(str(row.case_id) if row.case_id else None, row.revision)

    @sync_to_async
    def get_case(self, owner_id: str, case_id: str) -> CaseView | None:
        return self._view(owner_id, case_id)

    def _view(self, owner_id: str, case_id: str) -> CaseView | None:
        try:
            identifier = UUID(case_id)
        except ValueError:
            return None
        row = GrievanceCase.objects.filter(owner_id=owner_id, pk=identifier).first()
        if row is None:
            return None
        history = [dict(rev.facts) for rev in CaseFactRevision.objects.filter(case=row).order_by('revision')]
        return CaseView(str(row.pk), row.label, row.status, row.revision, history[-1] if history else {}, history,
                        list(CaseMessage.objects.filter(case=row).order_by('pk').values_list('text', flat=True)))

    @sync_to_async
    def contains_private_material(self, owner_id: str, text: str) -> bool:
        """Keep known private identifiers out of a mistakenly classified public turn."""
        candidate = text.casefold()
        for row in GrievanceCase.objects.filter(owner_id=owner_id):
            revisions = CaseFactRevision.objects.filter(case=row)
            for revision in revisions:
                for key, value in revision.facts.items():
                    if key in ('order_reference', 'customer_name', 'address', 'correspondence',
                               'amount_paid', 'refund_amount', 'purchase_date', 'delivery_date'):
                        if value and value.casefold() in candidate:
                            return True
        return False

    @sync_to_async
    def replay(self, turn: OwnerTurn) -> str | None:
        receipt = CaseOperationReceipt.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
                                                     delivery_id=turn.delivery_id).first()
        if receipt is None:
            return None
        return self._receipt_text(receipt)

    def _receipt_text(self, receipt: CaseOperationReceipt) -> str:
        if receipt.case_id and not GrievanceCase.objects.filter(owner_id=receipt.owner_id, pk=receipt.case_id).exists():
            return 'This delivery was already processed. Its case is no longer available; nothing was recreated.'
        return 'This delivery was already processed. No additional changes were made. Ask to show or list your saved cases.'

    @sync_to_async
    def apply(self, turn: OwnerTurn, action: IntakeAction, selection: SelectionView, current: CaseView | None) -> str:
        with transaction.atomic():
            selected = CaseSelection.objects.select_for_update().get(owner_id=turn.owner_id, conversation_id=turn.conversation_id)
            receipt = CaseOperationReceipt.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
                                                         delivery_id=turn.delivery_id).first()
            if receipt:
                return self._receipt_text(receipt)
            if selected.revision != selection.revision:
                return 'Case selection changed while this request was running. Nothing changed; please send it again.'
            case: GrievanceCase | None = None
            target = action.case_id or selection.case_id
            if target:
                try:
                    case = GrievanceCase.objects.select_for_update().filter(owner_id=turn.owner_id, pk=UUID(target)).first()
                except ValueError:
                    case = None
            if current and (case is None or str(case.pk) != current.id or case.revision != current.revision) and action.action in ('update', 'show'):
                return 'Case facts changed while this request was running. Nothing changed; please send it again.'
            reply: str
            outcome = 'unchanged'
            if action.action == 'create':
                case, created = GrievanceCase.objects.get_or_create(owner_id=turn.owner_id,
                    label_key=' '.join(action.label.casefold().split()), defaults={'label': action.label})
                if not created:
                    existing = CaseFactRevision.objects.get(case=case, revision=case.revision).facts
                    if any(existing.get(f.key) != f.value for f in action.facts):
                        return 'A case with that label already exists. Select it to update facts, or supply a distinct label for a different case.'
                selected.case = case
                selected.revision += 1
                selected.save(update_fields=['case', 'revision'])
                if created:
                    self._revise(case, {}, action, 'create')
                reply = ('Case saved and selected. ' if created else 'Existing case selected; no duplicate was created. ') + self.describe(case)
                outcome = 'saved' if created else 'existing'
            elif action.action == 'select':
                if case is None:
                    return 'That case is unavailable. Please select one of your listed cases.'
                selected.case = case
                selected.revision += 1
                selected.save(update_fields=['case', 'revision'])
                reply = 'Case selected. ' + self.describe(case)
            elif action.action == 'leave':
                selected.case = None
                selected.revision += 1
                selected.save(update_fields=['case', 'revision'])
                case = None
                reply = 'General research mode selected. Saved cases are preserved. Send a public research question separately.'
            elif action.action == 'list':
                labels = GrievanceCase.objects.filter(owner_id=turn.owner_id).order_by('created_at')
                reply = 'Your cases:\n' + '\n'.join(f'{row.label} [{row.pk}]' for row in labels) if labels.exists() else 'No saved cases.'
                case = None
            elif action.action in ('update', 'show') and case is not None:
                facts = dict(CaseFactRevision.objects.get(case=case, revision=case.revision).facts)
                conflicts = [fact.key for fact in action.facts if fact.key in facts and facts[fact.key] != fact.value]
                if conflicts and not action.correction_quote:
                    reply = 'Please confirm an explicit correction for: ' + ', '.join(conflicts) + '. Current facts are unchanged.'
                    outcome = 'conflict'
                else:
                    if action.action == 'update' and any(facts.get(f.key) != f.value for f in action.facts):
                        self._revise(case, facts, action, 'correction' if conflicts else 'intake')
                        outcome = 'saved'
                    reply = ('Facts saved. ' if outcome == 'saved' else '') + self.describe(case)
            else:
                case = None
                reply = 'Please create or explicitly select a case, or clarify the case and requested action. Drafting, research and case outcomes are not available yet.'
            if case is not None and action.action in ('create', 'update', 'show') and outcome != 'existing':
                CaseMessage.objects.create(case=case, role='user', text=turn.text)
                CaseMessage.objects.create(case=case, role='assistant', text=reply)
            CaseOperationReceipt.objects.create(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
                delivery_id=turn.delivery_id, action=action.action, case_id=case.pk if case else None,
                revision=case.revision if case else 0, outcome=outcome)
            return reply

    def _revise(self, case: GrievanceCase, facts: dict[str, str], action: IntakeAction, kind: str) -> None:
        facts.update({fact.key: fact.value for fact in action.facts})
        case.revision += 1
        case.save(update_fields=['revision'])
        CaseFactRevision.objects.create(case=case, revision=case.revision, facts=facts,
            sources={fact.key: fact.quote for fact in action.facts}, kind=kind)

    def describe(self, case: GrievanceCase) -> str:
        facts = CaseFactRevision.objects.get(case=case, revision=case.revision).facts
        missing = [field.replace('_', ' ') for field in MATERIAL_FIELDS if field not in facts]
        body = f'{case.label} [{case.pk}] — {case.status}.\n' + '\n'.join(f'{key.replace("_", " ")}: {value}' for key, value in facts.items())
        if missing:
            body += '\nPlease supply missing details when known: ' + ', '.join(missing) + '.'
        return body

"""Owner-scoped projections and short, revision-checked atomic case mutations."""

from dataclasses import dataclass
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import transaction

from core.turns import OwnerTurn
from grievances.constants import DRAFT_MATERIAL_FIELDS, MATERIAL_FIELDS
from grievances.models import CaseOutcome, CaseFactRevision, CaseMessage, CaseOperationReceipt, CaseSelection, CompanyReply, SentReport, CaseResearch, CaseResearchBrief, ComplaintDraft, GrievanceCase
from grievances.schema import IntakeAction
from grievances.ownership import CaseOwnership


@dataclass(frozen=True)
class CaseSummary:
    id: str
    label: str


@dataclass(frozen=True)
class SelectionView:
    case_id: str | None
    revision: int
    deletion_epoch: int = 0


@dataclass(frozen=True)
class CaseView:
    id: str
    label: str
    status: str
    revision: int
    facts: dict[str, str]
    history: list[dict[str, str]]
    messages: list[str]
    progress_revision: int = 0


@dataclass(frozen=True)
class DraftContent:
    subject: str
    body: str
    missing_fields: tuple[str, ...]
    evidence_revision: int | None = None
    observed_evidence_revision: int | None = None
    brief_revision: int | None = None
    brief_confirmed: bool | None = None
    kind: str = 'initial'
    reply_ids: tuple[int, ...] = ()
    sent_report_ids: tuple[int, ...] = ()
    progress_revision: int = 0


@dataclass(frozen=True)
class DraftView:
    id: str
    version: int
    fact_revision: int
    subject: str
    body: str
    missing_fields: tuple[str, ...]
    outdated: bool
    current: bool
    evidence_revision: int | None = None

    kind: str = 'initial'
    reply_ids: tuple[int, ...] = ()
    sent_report_ids: tuple[int, ...] = ()
    progress_revision: int = 0
    sent: bool = False

    def describe(self) -> str:
        state = 'outdated' if self.outdated else ('current usable draft' if self.current else 'saved version')
        if self.missing_fields:
            state += '; incomplete — please supply: ' + ', '.join(field.replace('_', ' ') for field in self.missing_fields)
        delivery = 'owner-reported sent' if self.sent else 'unsent'
        return f'Draft version {self.version} ({state}; {delivery}).\nSubject: {self.subject}\n\n{self.body}'


class CaseStore:
    @sync_to_async
    def list_cases(self, owner_id: str) -> list[CaseSummary]:
        return [CaseSummary(str(row.pk), row.label) for row in GrievanceCase.objects.filter(owner_id=owner_id).order_by('created_at')]

    @sync_to_async
    def selection(self, owner_id: str, conversation_id: str) -> SelectionView:
        with transaction.atomic():
            owner = CaseOwnership.lock(owner_id)
            row, _ = CaseSelection.objects.get_or_create(owner_id=owner_id, conversation_id=conversation_id)
            return SelectionView(str(row.case_id) if row.case_id else None, row.revision, owner.deletion_epoch)

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
                        list(CaseMessage.objects.filter(case=row).order_by('pk').values_list('text', flat=True)), row.progress_revision)

    @sync_to_async
    def contains_private_material(self, owner_id: str, text: str) -> bool:
        """Keep known private identifiers out of a mistakenly classified public turn."""
        return self._contains_private_material(owner_id, text)

    @sync_to_async
    def general_allowed(self, turn: OwnerTurn, epoch: int) -> bool:
        """Deletion and privacy classification share one short serialization boundary."""
        with transaction.atomic():
            owner = CaseOwnership.lock(turn.owner_id)
            return owner.deletion_epoch == epoch and not self._contains_private_material(turn.owner_id, turn.text)

    def _contains_private_material(self, owner_id: str, text: str) -> bool:
        candidate = text.casefold()
        for row in GrievanceCase.objects.filter(owner_id=owner_id):
            revisions = CaseFactRevision.objects.filter(case=row)
            for revision in revisions:
                for key, value in revision.facts.items():
                    if key in ('order_reference', 'customer_name', 'address', 'correspondence',
                               'amount_paid', 'refund_amount', 'purchase_date', 'delivery_date'):
                        if value and value.casefold() in candidate:
                            return True
        # Literal case-owned correspondence and generated text are private too,
        # even when they introduce no structured order/address fact.
        content = (
            CaseMessage.objects.filter(case__owner_id=owner_id).values_list('text', flat=True),
            CompanyReply.objects.filter(case__owner_id=owner_id).values_list('text', flat=True),
            SentReport.objects.filter(case__owner_id=owner_id).values_list('details', flat=True),
            ComplaintDraft.objects.filter(case__owner_id=owner_id).values_list('body', flat=True),
            ComplaintDraft.objects.filter(case__owner_id=owner_id).values_list('subject', flat=True),
            CaseOutcome.objects.filter(case__owner_id=owner_id).values_list('owner_quote', flat=True),
        )
        return any(value and value.casefold() in candidate for values in content for value in values)

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
        if receipt.action == 'record_reply':
            phase = OwnerTurn(receipt.owner_id, receipt.conversation_id, receipt.delivery_id, '').followup()
            draft = ComplaintDraft.objects.filter(operation__owner_id=receipt.owner_id,
                operation__conversation_id=receipt.conversation_id, operation__delivery_id=phase.delivery_id).first()
            if draft:
                return f'This delivery was already processed. Company reply saved; follow-up draft version {draft.version} is saved. No duplicate changes were made.'
            return 'This delivery was already processed. Company reply saved; no follow-up draft was saved for this delivery. Request a follow-up if needed.'
        if receipt.action == 'draft' and receipt.case_id:
            draft = ComplaintDraft.objects.filter(operation=receipt).first()
            if draft:
                return f'This delivery was already processed. Draft version {draft.version} is saved; ask to read that version. No additional changes were made.'
        return 'This delivery was already processed. No additional changes were made. Ask to show or list your saved cases.'

    @sync_to_async
    def apply(self, turn: OwnerTurn, action: IntakeAction, selection: SelectionView, current: CaseView | None) -> str:
        with transaction.atomic():
            owner = CaseOwnership.lock(turn.owner_id)
            if owner.deletion_epoch != selection.deletion_epoch:
                return 'Case content was deleted while this request was running. Nothing was saved.'
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
                reply = 'Please create or explicitly select a case, or clarify the case and requested action. Please specify whether to inspect facts or draft a refund email. For official research, review and confirm the public fields and official site first. You can report a sent version, save a company reply, request a follow-up or explicitly record an outcome. External sending is not available.'
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


    def _draft_views(self, owner_id: str, case_id: str) -> list[DraftView]:
        case = self._view(owner_id, case_id)
        if case is None:
            return []
        rows = list(ComplaintDraft.objects.filter(case_id=case.id).order_by('version'))
        latest_evidence = CaseResearch.objects.filter(case_id=case.id).order_by('-revision').first()
        latest_brief = CaseResearchBrief.objects.filter(case_id=case.id).order_by('-revision').first()
        latest_reply = CompanyReply.objects.filter(case_id=case.id).order_by('-pk').first()
        sent_drafts = set(SentReport.objects.filter(case_id=case.id).values_list('draft_id', flat=True))
        outdated: dict[int, bool] = {}
        for row in rows:
            source = case.history[row.fact_revision - 1]
            outdated[row.pk] = (row.kind == 'follow_up' and latest_reply is not None and latest_reply.pk not in row.reply_ids) or (row.evidence_revision is not None and (latest_evidence is None
                or row.evidence_revision != latest_evidence.revision or latest_brief is None
                or not latest_brief.confirmed or latest_evidence.brief_revision != latest_brief.revision)) or any(
                any(later.get(key) != source.get(key) for key in DRAFT_MATERIAL_FIELDS)
                for later in case.history[row.fact_revision:])
        usable = [row.version for row in rows if not outdated[row.pk] and not row.missing_fields]
        current_version = max(usable, default=0)
        return [DraftView(str(row.pk), row.version, row.fact_revision, row.subject, row.body,
                          tuple(row.missing_fields), outdated[row.pk], row.version == current_version, row.evidence_revision, row.kind, tuple(row.reply_ids),
                          tuple(row.sent_report_ids), row.progress_revision, row.pk in sent_drafts) for row in rows]

    @sync_to_async
    def list_drafts(self, owner_id: str, case_id: str) -> list[DraftView]:
        return self._draft_views(owner_id, case_id)

    @sync_to_async
    def get_draft(self, owner_id: str, case_id: str, version: int | None = None) -> DraftView | None:
        return next((draft for draft in self._draft_views(owner_id, case_id)
                     if (draft.version == version if version is not None else draft.current)), None)

    @sync_to_async
    def read_drafts(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, version: int | None, *, listing: bool) -> str:
        with transaction.atomic():
            owner = CaseOwnership.lock(turn.owner_id)
            if owner.deletion_epoch != selection.deletion_epoch:
                return 'Case content was deleted while this request was running. Nothing was saved.'
            selected = CaseSelection.objects.select_for_update().get(owner_id=turn.owner_id, conversation_id=turn.conversation_id)
            case = GrievanceCase.objects.select_for_update().filter(owner_id=turn.owner_id, pk=current.id).first()
            if (selected.revision != selection.revision or str(selected.case_id) != current.id
                    or case is None or case.revision != current.revision):
                return 'Case state changed while this request was running. Please request the drafts again.'
            drafts = self._draft_views(turn.owner_id, current.id)
            if listing:
                if not drafts:
                    return 'No saved drafts for this case.'
                return '\n'.join(f'Version {draft.version}: ' +
                    ('outdated' if draft.outdated else ('current usable draft' if draft.current else 'saved version')) +
                    ('; incomplete' if draft.missing_fields else '') + ('; owner-reported sent' if draft.sent else '; unsent') for draft in drafts)
            draft = next((item for item in drafts if (item.version == version if version is not None else item.current)), None)
            return draft.describe() if draft else 'No current usable draft or requested version is available. List drafts to inspect incomplete or outdated versions.'

    @sync_to_async
    def save_draft(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, content: DraftContent) -> str:
        with transaction.atomic():
            owner = CaseOwnership.lock(turn.owner_id)
            if owner.deletion_epoch != selection.deletion_epoch:
                return 'Case content was deleted while this request was running. Nothing was saved.'
            selected = CaseSelection.objects.select_for_update().get(owner_id=turn.owner_id, conversation_id=turn.conversation_id)
            receipt = CaseOperationReceipt.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id, delivery_id=turn.delivery_id).first()
            if receipt:
                return self._receipt_text(receipt)
            case = GrievanceCase.objects.select_for_update().filter(owner_id=turn.owner_id, pk=current.id).first()
            if selected.revision != selection.revision or str(selected.case_id) != current.id:
                return 'Case selection changed while drafting. No draft was saved; please request it again.'
            if case is None or case.revision != current.revision:
                return 'Case facts changed or the case was deleted while drafting. No draft was saved; please request it again.'
            if case.progress_revision != content.progress_revision:
                return 'Case progress changed while drafting. No draft was saved; please request it again.'
            evidence = CaseResearch.objects.filter(case=case).order_by('-revision').first()
            brief = CaseResearchBrief.objects.filter(case=case).order_by('-revision').first()
            if ((brief.revision if brief else None) != content.brief_revision
                    or (brief.confirmed if brief else None) != content.brief_confirmed):
                return 'Public research fields changed while drafting. No draft was saved; please request it again.'
            if (evidence.revision if evidence else None) != content.observed_evidence_revision:
                return 'Research evidence changed while drafting. No draft was saved; please request it again.'
            latest = ComplaintDraft.objects.filter(case=case).order_by('-version').first()
            version = latest.version + 1 if latest else 1
            receipt = CaseOperationReceipt.objects.create(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
                delivery_id=turn.delivery_id, action='draft', case_id=case.pk, revision=current.revision, outcome='saved')
            row = ComplaintDraft.objects.create(case=case, version=version, fact_revision=current.revision, operation=receipt,
                subject=content.subject, body=content.body, missing_fields=list(content.missing_fields), evidence_revision=content.evidence_revision, kind=content.kind, reply_ids=list(content.reply_ids),
                sent_report_ids=list(content.sent_report_ids), progress_revision=content.progress_revision)
            return DraftView(str(row.pk), version, current.revision, content.subject, content.body, content.missing_fields,
                             False, not content.missing_fields, content.evidence_revision, content.kind, content.reply_ids,
                             content.sent_report_ids, content.progress_revision).describe()

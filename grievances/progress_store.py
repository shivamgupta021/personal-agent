"""Owner-scoped manual progress; sending is only a report, never an external action."""

from dataclasses import dataclass
import re

from asgiref.sync import sync_to_async
from django.db import transaction

from core.turns import OwnerTurn
from grievances.ownership import CaseOwnership
from grievances.correspondence import ParsedCompanyReply
from grievances.models import CaseOutcome, CompanyReply, CaseOperationReceipt, CaseSelection, ComplaintDraft, GrievanceCase, SentReport
from grievances.store import CaseView, SelectionView


@dataclass(frozen=True)
class SentReportView:
    id: int
    draft_version: int
    details: str
    recorded_at: str
    draft_subject: str
    draft_body: str
    draft_fact_revision: int


@dataclass(frozen=True)
class CompanyReplyView:
    id: int
    text: str
    recorded_at: str


@dataclass(frozen=True)
class OutcomeView:
    id: int
    outcome: str
    owner_quote: str
    recorded_at: str


@dataclass(frozen=True)
class ProgressSnapshot:
    revision: int
    sent: list[SentReportView]
    replies: list[CompanyReplyView]
    outcomes: list[OutcomeView]


@dataclass(frozen=True)
class SentSaveResult:
    saved: bool
    message: str
    version: int | None = None


@dataclass(frozen=True)
class ReplySaveResult:
    saved: bool
    message: str


class ProgressChanged(Exception):
    pass


class ProgressStore:
    @sync_to_async
    def snapshot(self, owner_id: str, case_id: str) -> ProgressSnapshot | None:
        case = GrievanceCase.objects.filter(owner_id=owner_id, pk=case_id).first()
        if case is None:
            return None
        sent = [SentReportView(row.pk, row.draft.version, row.details, row.recorded_at.isoformat(),
                               row.draft.subject, row.draft.body, row.draft.fact_revision)
                for row in SentReport.objects.filter(case=case).select_related('draft').order_by('pk')]
        replies = [CompanyReplyView(row.pk, row.text, row.recorded_at.isoformat())
                   for row in CompanyReply.objects.filter(case=case).order_by('pk')]
        outcomes = [OutcomeView(row.pk, row.outcome, row.owner_quote, row.recorded_at.isoformat())
                    for row in CaseOutcome.objects.filter(case=case).order_by('pk')]
        return ProgressSnapshot(case.progress_revision, sent, replies, outcomes)

    @staticmethod
    def _lock(turn: OwnerTurn, selection: SelectionView, current: CaseView) -> GrievanceCase:
        owner = CaseOwnership.lock(turn.owner_id)
        if owner.deletion_epoch != selection.deletion_epoch:
            raise ProgressChanged
        selected = CaseSelection.objects.select_for_update().get(owner_id=turn.owner_id, conversation_id=turn.conversation_id)
        case = GrievanceCase.objects.select_for_update().filter(owner_id=turn.owner_id, pk=current.id).first()
        if case is not None and CaseOperationReceipt.objects.filter(owner_id=turn.owner_id,
                conversation_id=turn.conversation_id, delivery_id=turn.delivery_id).exists():
            return case
        if (case is None or case.revision != current.revision or case.progress_revision != current.progress_revision
                or selected.revision != selection.revision or str(selected.case_id) != current.id):
            raise ProgressChanged
        return case

    @staticmethod
    def _receipt(turn: OwnerTurn, current: CaseView, action: str) -> CaseOperationReceipt:
        return CaseOperationReceipt.objects.create(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
            delivery_id=turn.delivery_id, case_id=current.id, revision=current.revision, action=action, outcome='saved')

    @staticmethod
    def _advance(case: GrievanceCase) -> None:
        case.progress_revision += 1
        case.save(update_fields=['progress_revision'])

    @sync_to_async
    def record_sent(self, turn: OwnerTurn, selection: SelectionView, current: CaseView,
                    version: int | None, intent_quote: str, details: str) -> SentSaveResult:
        if (not intent_quote or intent_quote not in turn.text or not re.match(r'^I (?:have )?sent\b', turn.text.strip(), re.IGNORECASE)
                or (details and details not in turn.text)):
            return SentSaveResult(False, 'Please explicitly report that you sent a particular draft version. No sent report was saved.')
        with transaction.atomic():
            case = self._lock(turn, selection, current)
            if CaseOperationReceipt.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id, delivery_id=turn.delivery_id).exists():
                return SentSaveResult(False, 'This sent report was already processed.')
            drafts = list(ComplaintDraft.objects.filter(case=case).order_by('version'))
            draft: ComplaintDraft | None
            if version is None:
                if len(drafts) != 1 or re.search(r'\b(?:version|draft)\s*#?\d+', turn.text, re.IGNORECASE):
                    return SentSaveResult(False, 'Which draft version did you send? Please explicitly report the version; nothing was recorded.')
                draft = drafts[0]
            else:
                if not re.search(rf'\b(?:version|draft(?: version)?)\s*#?{version}\b', intent_quote, re.IGNORECASE):
                    return SentSaveResult(False, 'Please explicitly state the sent draft version. Nothing was recorded.')
                draft = next((row for row in drafts if row.version == version), None)
                if draft is None:
                    return SentSaveResult(False, 'That draft version is unavailable in this case. Nothing was recorded.')
            receipt = self._receipt(turn, current, 'record_sent')
            SentReport.objects.create(case=case, draft=draft, operation=receipt, details=details)
            self._advance(case)
            return SentSaveResult(True, f'Recorded owner-reported sending of draft version {draft.version}. The case outcome is unchanged.', draft.version)


    @sync_to_async
    def record_reply(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, text: str, parsed: ParsedCompanyReply | None) -> ReplySaveResult:
        if not text or parsed is None or parsed.body != text:
            return ReplySaveResult(False, 'Please paste the complete company reply after “Company reply:”, quoting it if you also include a request. No reply was saved.')
        with transaction.atomic():
            case = self._lock(turn, selection, current)
            if CaseOperationReceipt.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id, delivery_id=turn.delivery_id).exists():
                return ReplySaveResult(True, 'This company reply was already saved.')
            receipt = self._receipt(turn, current, 'record_reply')
            reply = CompanyReply.objects.create(case=case, operation=receipt, text=text)
            self._advance(case)
            return ReplySaveResult(True, f'Company reply {reply.pk} saved exactly as supplied. The case outcome is unchanged.')

    @sync_to_async
    def record_outcome(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, outcome: str, intent_quote: str) -> str:
        patterns = {
            'resolved': r'^(?:I consider (?:this|the)(?: case)? resolved|mark (?:this|the)(?: case)? resolved)',
            'closed_without_resolution': r'^close (?:this|the)(?: case)? without resolution',
        }
        pattern = patterns.get(outcome)
        if (not pattern or not intent_quote or intent_quote not in turn.text
                or not re.match(pattern + r'(?:[.!]|$)', turn.text.strip(), re.IGNORECASE)):
            return 'Please explicitly state your own outcome: “I consider this case resolved” or “Close this case without resolution”. Nothing was changed.'
        with transaction.atomic():
            case = self._lock(turn, selection, current)
            if CaseOperationReceipt.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id, delivery_id=turn.delivery_id).exists():
                return 'This outcome was already recorded.'
            if case.status == outcome:
                return 'This case already has that owner-reported outcome.'
            receipt = self._receipt(turn, current, 'outcome')
            CaseOutcome.objects.create(case=case, operation=receipt, outcome=outcome, owner_quote=intent_quote)
            case.status = outcome
            case.save(update_fields=['status'])
            self._advance(case)
            return 'Recorded your explicit case outcome: ' + outcome.replace('_', ' ') + '.'


    @sync_to_async
    def reply_saved(self, turn: OwnerTurn) -> bool:
        return CompanyReply.objects.filter(operation__owner_id=turn.owner_id, operation__conversation_id=turn.conversation_id,
                                           operation__delivery_id=turn.delivery_id).exists()

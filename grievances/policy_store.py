"""Case-owned reviewed public briefs and immutable policy evidence snapshots."""

from dataclasses import asdict, dataclass

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from core.turns import OwnerTurn
from grievances.models import CaseOperationReceipt, CaseResearch, CaseResearchBrief, CaseSelection, GrievanceCase
from grievances.policy_records import PolicySource, ResearchFinding
from grievances.schema import PublicResearchBrief
from grievances.store import CaseView, SelectionView


class PolicyStateChanged(Exception):
    pass


@dataclass(frozen=True)
class ResearchBriefView:
    id: int
    revision: int
    fact_revision: int
    company: str
    product: str
    issue: str
    jurisdiction: str
    official_site: str
    confirmed: bool

    def public_fields(self) -> dict[str, str]:
        return {key: str(getattr(self, key)) for key in ('company', 'product', 'issue', 'jurisdiction', 'official_site')}


@dataclass(frozen=True)
class ResearchView:
    id: int
    revision: int
    fact_revision: int
    brief_revision: int
    status: str
    sources: list[PolicySource]
    findings: list[ResearchFinding]
    uncertainty: str


class PolicyStore:
    @staticmethod
    def _brief(row: CaseResearchBrief) -> ResearchBriefView:
        return ResearchBriefView(row.pk, row.revision, row.fact_revision, **row.fields, confirmed=row.confirmed)

    @sync_to_async
    def latest_brief(self, owner_id: str, case_id: str) -> ResearchBriefView | None:
        row = CaseResearchBrief.objects.filter(case_id=case_id, case__owner_id=owner_id).order_by('-revision').first()
        return self._brief(row) if row else None

    @sync_to_async
    def list_research(self, owner_id: str, case_id: str) -> list[ResearchView]:
        return [ResearchView(row.pk, row.revision, row.fact_revision, row.brief_revision, row.status,
                             [PolicySource(**source) for source in row.sources],
                             [ResearchFinding(**finding) for finding in row.findings], row.uncertainty)
                for row in CaseResearch.objects.filter(case_id=case_id, case__owner_id=owner_id).order_by('revision')]

    @staticmethod
    def _lock(turn: OwnerTurn, selection: SelectionView, current: CaseView) -> GrievanceCase:
        selected = CaseSelection.objects.select_for_update().get(owner_id=turn.owner_id, conversation_id=turn.conversation_id)
        case = GrievanceCase.objects.select_for_update().filter(owner_id=turn.owner_id, pk=current.id).first()
        if (case is None or case.revision != current.revision or selected.revision != selection.revision
                or str(selected.case_id) != current.id):
            raise PolicyStateChanged
        return case

    @staticmethod
    def _processed(turn: OwnerTurn) -> bool:
        return CaseOperationReceipt.objects.filter(owner_id=turn.owner_id, conversation_id=turn.conversation_id, delivery_id=turn.delivery_id).exists()

    @staticmethod
    def _receipt(turn: OwnerTurn, current: CaseView, action: str) -> None:
        CaseOperationReceipt.objects.create(owner_id=turn.owner_id, conversation_id=turn.conversation_id,
            delivery_id=turn.delivery_id, case_id=current.id, revision=current.revision, action=action, outcome='saved')

    @sync_to_async
    def propose(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, brief: PublicResearchBrief) -> str:
        with transaction.atomic():
            case = self._lock(turn, selection, current)
            if self._processed(turn):
                return 'This public research review was already processed.'
            latest = CaseResearchBrief.objects.filter(case=case).order_by('-revision').first()
            CaseResearchBrief.objects.create(case=case, revision=latest.revision + 1 if latest else 1,
                                             fact_revision=current.revision, fields=brief.model_dump())
            self._receipt(turn, current, 'review_research')
            fields = '\n'.join(f'{key.replace("_", " ")}: {value}' for key, value in brief.model_dump().items())
            return ('Review these public fields before anything is sent to web research:\n' + fields +
                    '\nIf these contain only public company information and this is the company’s official site, say '
                    '“I confirm these public research fields and this official site.” Otherwise supply corrected fields. '
                    'A pasted URL alone is not confirmation.')

    @sync_to_async
    def confirm(self, turn: OwnerTurn, selection: SelectionView, current: CaseView) -> str:
        with transaction.atomic():
            case = self._lock(turn, selection, current)
            if self._processed(turn):
                return 'This confirmation was already processed.'
            latest = CaseResearchBrief.objects.filter(case=case).order_by('-revision').first()
            if latest is None or latest.fact_revision != current.revision:
                return 'Please review the current public research fields and official site first.'
            latest.confirmed = True
            latest.confirmed_at = timezone.now()
            latest.save(update_fields=['confirmed', 'confirmed_at'])
            self._receipt(turn, current, 'confirm_research')
            return 'Public research fields and official-site anchor recorded as owner-confirmed. Request official policy research next; site identity and links still need checking.'

    @sync_to_async
    def save(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, brief: ResearchBriefView,
             status: str, sources: list[PolicySource], findings: list[ResearchFinding], uncertainty: str) -> str:
        with transaction.atomic():
            case = self._lock(turn, selection, current)
            if self._processed(turn):
                return 'This research delivery was already processed; no duplicate evidence was saved.'
            latest_brief = CaseResearchBrief.objects.filter(case=case).order_by('-revision').first()
            if latest_brief is None or latest_brief.pk != brief.id or not latest_brief.confirmed:
                raise PolicyStateChanged
            latest = CaseResearch.objects.filter(case=case).order_by('-revision').first()
            revision = latest.revision + 1 if latest else 1
            CaseResearch.objects.create(case=case, revision=revision, fact_revision=current.revision,
                brief_revision=brief.revision, status=status, sources=[asdict(source) for source in sources],
                findings=[asdict(finding) for finding in findings], uncertainty=uncertainty)
            self._receipt(turn, current, 'research')
            return f'Research revision {revision}: {status}. {uncertainty} You can now request a draft; factual drafting remains available.'

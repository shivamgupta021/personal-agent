"""Challenge graders with wrong answers and wrong state, not only happy paths."""
from pathlib import Path

import pytest

from core.journey_evaluation import JOURNEYS, JourneyGrader
from grievances.acceptance_evidence import AcceptanceFingerprint


@pytest.mark.parametrize('failure', ['wrong_preference', 'wrong_date', 'missing_answer', 'unrequested_web', 'internal_id'])
def test_journey_grader_rejects_known_failures(failure: str) -> None:
    journey = next(j for j in JOURNEYS if j.name == 'mixed-inspection')
    transcript = [{'answer': '2026-11-20; short replies'}]
    preferences = ['short replies']
    facts = {'purchase_date': '2026-11-20'}
    public: list[str] = []
    if failure == 'wrong_preference':
        preferences = ['detailed replies']
    elif failure == 'wrong_date':
        facts['purchase_date'] = '2026-11-21'
    elif failure == 'missing_answer':
        transcript[0]['answer'] = 'I can help with refunds.'
    elif failure == 'unrequested_web':
        public.append('unnecessary search')
    else:
        transcript[0]['answer'] += ' [a6d24e01-d82a-4017-bef1-137b33a019bd]'
    assert not all(JourneyGrader.grade(journey, transcript, preferences, facts, public).values())


def test_journey_grader_accepts_equivalent_date_format() -> None:
    journey = next(j for j in JOURNEYS if j.name == 'case-correction')
    transcript = [{'answer': 'Updated.'}, {'answer': '21 November 2026; short replies'}]
    assert all(JourneyGrader.grade(journey, transcript, ['short replies'], {'purchase_date': '2026-11-21'}, []).values())


@pytest.mark.parametrize('app', ['gmail', 'attachments'])
def test_acceptance_evidence_expires_when_integrated_app_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: str) -> None:
    monkeypatch.setattr(AcceptanceFingerprint, 'ROOT', tmp_path)
    (tmp_path / 'pyproject.toml').write_text('')
    (tmp_path / 'uv.lock').write_text('')
    folder = tmp_path / app
    folder.mkdir()
    path = folder / 'service.py'
    path.write_text('before')
    recorded = AcceptanceFingerprint.current()
    path.write_text('after')
    with pytest.raises(ValueError, match='do not match'):
        AcceptanceFingerprint.require(recorded, live=True)


@pytest.mark.parametrize('answer', ['Not recorded yet', 'INR 1840'])
def test_receipt_grader_requires_source_evidence(answer: str) -> None:
    journey = next(j for j in JOURNEYS if j.name == 'receipt-followup')
    assert not all(JourneyGrader.grade(journey, [{'answer': answer}], ['short replies'],
        {'purchase_date': '2026-11-20'}, []).values())


def test_reply_check_grader_rejects_static_fact_inspection() -> None:
    journey = next(j for j in JOURNEYS if j.name == 'incoming-reply-check')
    transcript = [{'answer': 'Correspondence: Not recorded yet',
        'proposals': [{'tool': 'inspect_case', 'arguments': {'fields': ['correspondence']}}]}]
    assert not all(JourneyGrader.grade(journey, transcript, ['short replies'],
        {'purchase_date': '2026-11-20'}, []).values())

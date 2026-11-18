from research.evaluation.experiment import ResearchScores


def test_evidence_failure_is_not_hidden_by_passing_answer() -> None:
    scores = ResearchScores().evaluate(output={
        'evidence_checks': {'required_content': False},
        'answer_checks': {'required_content': True},
        'passed': False, 'error': None,
    })
    by_name = {score.name: score for score in scores}
    assert by_name['evidence_checks_pass'].value == 0
    assert 'required_content' in (by_name['evidence_checks_pass'].comment or '')
    assert by_name['answer_checks_pass'].value == 1
    assert by_name['research_checks_pass'].value == 0


def test_execution_failure_fails_overall_score_despite_passing_checks() -> None:
    scores = ResearchScores().evaluate(output={
        'evidence_checks': {'required_content': True},
        'answer_checks': {'required_content': True},
        'passed': False, 'error': 'Research deadline exceeded.',
    })
    assert scores[-1].value == 0
    assert scores[-1].comment == 'Research deadline exceeded.'

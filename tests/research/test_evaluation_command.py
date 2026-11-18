import json
import os
import subprocess
import sys
from pathlib import Path


def test_evaluation_command_reports_evidence_and_answer_checks_separately(tmp_path: Path) -> None:
    report_path = tmp_path / 'report.json'
    result = subprocess.run(
        [sys.executable, 'manage.py', 'evaluate_research', '--output', str(report_path)],
        env={**os.environ, 'LANGFUSE_PUBLIC_KEY': '', 'LANGFUSE_SECRET_KEY': ''},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text())
    assert report['mode'] == 'replay'
    assert report['model_quality_assessed'] is False
    assert report['passed'] is True
    anchored = next(case for case in report['cases'] if case['id'] == 'anchored_policy')
    assert anchored['evidence_checks']['required_content'] is True
    assert anchored['answer_checks']['required_content'] is True
    assert anchored['answer_checks']['citations_read'] is True


def test_correct_looking_answer_cannot_hide_missing_evidence(tmp_path: Path) -> None:
    suite = json.loads(Path('research/evaluation/cases.json').read_text())
    case = suite['cases'][0]
    case['replay'] = [case['replay'][-1]]  # Keep the good answer, remove retrieval.
    suite['cases'] = [case]
    suite_path = tmp_path / 'unsupported.json'
    suite_path.write_text(json.dumps(suite))
    output = tmp_path / 'report.json'
    result = subprocess.run(
        [sys.executable, 'manage.py', 'evaluate_research', '--suite', str(suite_path), '--output', str(output)],
        env={**os.environ, 'LANGFUSE_PUBLIC_KEY': '', 'LANGFUSE_SECRET_KEY': ''},
        capture_output=True, text=True, timeout=15,
    )
    report = json.loads(output.read_text())
    assert result.returncode == 1
    assert report['passed'] is False
    assert report['cases'][0]['answer_checks']['required_content'] is True
    assert report['cases'][0]['answer_checks']['citations_read'] is False
    assert report['cases'][0]['evidence_checks']['required_content'] is False


def test_suite_covers_empty_unavailable_and_unanchored_retrieval(tmp_path: Path) -> None:
    output = tmp_path / 'report.json'
    result = subprocess.run(
        [sys.executable, 'manage.py', 'evaluate_research', '--output', str(output)],
        env={**os.environ, 'LANGFUSE_PUBLIC_KEY': '', 'LANGFUSE_SECRET_KEY': ''},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    cases = {case['id']: case for case in json.loads(output.read_text())['cases']}
    assert {'empty_search_recovery', 'unavailable_search', 'unanchored_policy'} <= cases.keys()
    assert cases['empty_search_recovery']['evidence_checks']['distinct_search_queries'] is True
    assert cases['unavailable_search']['evidence_checks']['search_failure_visible'] is True
    assert cases['unanchored_policy']['warnings'] == ['First page excerpt lacked the required evidence.']


def test_good_retrieval_cannot_hide_wrong_answer(tmp_path: Path) -> None:
    suite = json.loads(Path('research/evaluation/cases.json').read_text())
    case = suite['cases'][0]
    case['replay'][-1]['answer'] = 'Returns are allowed within 30 days. https://example.com/policy#returns'
    suite['cases'] = [case]
    suite_path = tmp_path / 'wrong-answer.json'
    suite_path.write_text(json.dumps(suite))
    output = tmp_path / 'report.json'
    result = subprocess.run(
        [sys.executable, 'manage.py', 'evaluate_research', '--suite', str(suite_path), '--output', str(output)],
        env={**os.environ, 'LANGFUSE_PUBLIC_KEY': '', 'LANGFUSE_SECRET_KEY': ''},
        capture_output=True, text=True, timeout=15,
    )
    report = json.loads(output.read_text())
    assert result.returncode == 1
    assert all(report['cases'][0]['evidence_checks'].values())
    assert report['cases'][0]['answer_checks']['required_content'] is False


def test_langfuse_experiment_requires_project_keys(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, 'manage.py', 'evaluate_research', '--langfuse', '--output', str(tmp_path / 'report.json')],
        env={**os.environ, 'LANGFUSE_PUBLIC_KEY': '', 'LANGFUSE_SECRET_KEY': ''},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1
    assert 'Configure both Langfuse project keys in .env' in result.stderr
    assert not (tmp_path / 'report.json').exists()

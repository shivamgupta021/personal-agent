"""Content hashes and independently collected evidence; no model judging."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
import xml.etree.ElementTree as ET

from grievances.constants import DETERMINISTIC_TIMEOUT_SECONDS


class AcceptanceFingerprint:
    ROOT = Path(__file__).resolve().parents[1]

    @staticmethod
    def digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

    @staticmethod
    def file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def provenance(cls) -> dict[str, Any]:
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=cls.ROOT, capture_output=True, text=True, check=True).stdout.strip()
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=cls.ROOT, capture_output=True, text=True, check=True).stdout
        return {'git_revision': revision, 'git_dirty': bool(status), 'recorded_at_utc': datetime.now(timezone.utc).isoformat()}

    @classmethod
    def current(cls) -> dict[str, str]:
        groups: dict[str, list[Path]] = {'runtime': [], 'prompts': [], 'fixtures': [], 'tests': []}
        for directory in ('core', 'grievances', 'gmail', 'attachments', 'conversations', 'preferences', 'research', 'subscriptions', 'telegram_bot', 'config'):
            for path in (cls.ROOT / directory).rglob('*.py'):
                if 'evaluation' in str(path.relative_to(cls.ROOT)) or 'acceptance' in path.name or 'fixtures' in path.name or path.name.startswith('evaluate_'):
                    groups['fixtures'].append(path)
                else:
                    groups['runtime'].append(path)
                if 'prompt' in path.name:
                    groups['prompts'].append(path)
        groups['runtime'] += [cls.ROOT / 'pyproject.toml', cls.ROOT / 'uv.lock']
        groups['tests'] = list((cls.ROOT / 'tests').rglob('*.py'))
        return {name + '_sha256': cls.digest({str(p.relative_to(cls.ROOT)): cls.file(p) for p in sorted(paths)})
                for name, paths in groups.items()}

    @classmethod
    def require(cls, recorded: dict[str, str], *, live: bool = False) -> None:
        current = cls.current()
        keys = ('runtime_sha256', 'prompts_sha256') if live else tuple(current)
        if any(recorded.get(key) != current[key] for key in keys):
            raise ValueError('Evidence hashes do not match the current source, prompts, fixtures or tests.')


class DeterministicEvidence:
    # Required concrete tests, including every relevant parametrized variant.
    TESTS = {
        'G09': ('tests/grievances/test_drafts.py::test_late_generation_cannot_save_after_case_state_changes[correction]',
                'tests/grievances/test_drafts.py::test_late_generation_cannot_save_after_case_state_changes[selection]'),
        'G10': ('tests/grievances/test_acceptance.py::test_committed_case_operations_survive_failed_telegram_delivery',),
        'G16': ('tests/grievances/test_intake.py::test_private_trace_payload_exclusion_with_global_content_capture[none]',
                'tests/grievances/test_intake.py::test_private_trace_payload_exclusion_with_global_content_capture[validation]',
                'tests/grievances/test_intake.py::test_private_trace_payload_exclusion_with_global_content_capture[provider]',
                'tests/grievances/test_policy.py::test_policy_spans_remain_private_when_public_capture_is_enabled'),
        'G20': ('tests/grievances/test_deletion.py::test_deletion_during_followup_cannot_save_or_return_private_result',
                'tests/grievances/test_deletion.py::test_inflight_create_cannot_commit_or_replay_after_deletion',
                'tests/grievances/test_deletion.py::test_failed_producer_flush_and_http_failure_remain_pending_after_restart',
                'tests/grievances/test_deletion.py::test_telegram_checks_prepared_reply_before_each_chunk'),
    }

    @classmethod
    def record(cls, destination: Path) -> dict[str, Any]:
        before = AcceptanceFingerprint.current()
        junit = destination.with_suffix('.junit.xml').resolve()
        log = destination.with_suffix('.pytest.log').resolve()
        command = [sys.executable, '-m', 'pytest', '-q', '--junitxml=' + str(junit)]
        with log.open('w') as output:
            result = subprocess.run(command, cwd=AcceptanceFingerprint.ROOT, stdout=output, stderr=subprocess.STDOUT,
                env={**os.environ, 'LANGFUSE_PUBLIC_KEY': '', 'LANGFUSE_SECRET_KEY': ''}, timeout=DETERMINISTIC_TIMEOUT_SECONDS, check=False)
        if result.returncode or before != AcceptanceFingerprint.current():
            raise ValueError('Deterministic checks failed or sources changed during testing; inspect the pytest log.')
        document = {'version': 1, **AcceptanceFingerprint.provenance(), 'fingerprints': before, 'command': command,
                    'artifact': {'path': str(junit), 'sha256': AcceptanceFingerprint.file(junit)}}
        destination.write_text(json.dumps(document, indent=2))
        cls.load(destination)
        return document

    @classmethod
    def load(cls, path: Path) -> dict[str, dict[str, bool]]:
        document = json.loads(path.read_text())
        if not isinstance(document, dict):
            raise ValueError('Deterministic manifest must be an object.')
        fingerprints = document.get('fingerprints')
        if not isinstance(fingerprints, dict) or any(not isinstance(value, str) for value in fingerprints.values()):
            raise ValueError('Deterministic fingerprints must be an object containing string hashes.')
        descriptor = document.get('artifact')
        if (not isinstance(descriptor, dict) or not isinstance(descriptor.get('path'), str)
                or not descriptor['path'] or not isinstance(descriptor.get('sha256'), str)):
            raise ValueError('Deterministic artifact must be an object with a path and checksum.')
        AcceptanceFingerprint.require(fingerprints)
        artifact = Path(descriptor['path'])
        if AcceptanceFingerprint.file(artifact) != descriptor['sha256']:
            raise ValueError('Deterministic artifact checksum mismatch.')
        try:
            root = ET.fromstring(artifact.read_text())
        except ET.ParseError:
            raise ValueError('Deterministic artifact must contain valid JUnit XML.') from None
        rows = list(root.iter('testcase'))
        if any(row.find(tag) is not None for row in rows for tag in ('failure', 'error', 'skipped')):
            raise ValueError('The deterministic suite contains failed or skipped tests.')
        checks: dict[str, dict[str, bool]] = {}
        for scenario, nodes in cls.TESTS.items():
            checks[scenario] = {}
            for node in nodes:
                file, test = node.split('::', 1)
                module = file.removesuffix('.py').replace('/', '.')
                matching = [row for row in rows if row.get('name') == test and row.get('classname') == module]
                checks[scenario][node] = len(matching) == 1 and not list(matching[0])
            if not all(checks[scenario].values()):
                raise ValueError('Required deterministic evidence is missing, failed or skipped: ' + scenario)
        return checks

import asyncio
import json
from typing import Any

import httpx2
from pathlib import Path

import pytest

from grievances.acceptance import AcceptanceGate
from grievances.service import CaseService
from core.turns import OwnerTurn
from telegram_bot.client import TelegramClient, TelegramError
from telegram_bot.service import TelegramService
from telegram_bot.state import PollingState
from tests.grievances.test_progress import FACTS, ProgressConversation
from grievances.acceptance_evidence import AcceptanceFingerprint, DeterministicEvidence
from grievances.acceptance_catalog import CASES, LIVE_CHECKS, REQUIRED_CHECKS, REVIEW_CRITERIA


def test_finalization_cannot_pass_without_bound_independent_reviews(tmp_path: Path) -> None:
    path = tmp_path / 'run.json'
    path.write_text(json.dumps({'version': 1, 'fingerprints': AcceptanceFingerprint.current(), 'outputs': [], 'url': 'synthetic'}))
    with pytest.raises(ValueError, match='20'):
        AcceptanceGate().finalize(path, tmp_path / 'reviews.json', tmp_path / 'deterministic.json', tmp_path / 'live.json')


@pytest.mark.django_db(transaction=True)
def test_committed_case_operations_survive_failed_telegram_delivery(tmp_path: Path) -> None:
    async def run() -> None:
        chat = ProgressConversation()
        updates: list[dict[str, Any]] = []
        sent: list[str] = []
        fail_send = True
        def telegram(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content)
            if request.url.path.endswith('getUpdates'):
                return httpx2.Response(200, json={'ok': True, 'result': [u for u in updates if u['update_id'] >= body['offset']][:1]})
            if fail_send:
                raise httpx2.ReadTimeout('Synthetic uncertain delivery', request=request)
            sent.append(body['text'])
            return httpx2.Response(200, json={'ok': True, 'result': {}})
        create = 'Create a case called Lumen kettle. ' + '\n'.join(FACTS.values())
        correction = 'Correction: amount paid and refund requested are INR 2,700'
        operations: list[tuple[str, dict[str, Any]]] = [
            (create, {'action': 'create', 'label': 'Lumen kettle', 'intent_quote': 'Create a case',
                      'facts': [{'key': key, 'value': value, 'quote': value} for key, value in FACTS.items()]}),
            (correction, {'action': 'update', 'correction_quote': correction,
                          'facts': [{'key': key, 'value': 'INR 2,700', 'quote': 'INR 2,700'} for key in ('amount_paid', 'refund_amount')]}),
            ('Draft my refund email', {'action': 'draft'}),
        ]
        async with httpx2.AsyncClient(base_url='https://telegram.invalid/', transport=httpx2.MockTransport(telegram)) as client:
            for index, (text, proposal) in enumerate(operations, 1):
                chat.action = proposal
                updates[:] = [{'update_id': index, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': text}}]
                poller = TelegramService(TelegramClient(client), PollingState(tmp_path), 7, chat.service.prepare, identity='alpha')
                with poller.state.locked():
                    with pytest.raises(TelegramError):
                        await poller.poll_once()
                requests = len(chat.inputs)
                restarted = CaseService(chat.service.model)
                poller = TelegramService(TelegramClient(client), PollingState(tmp_path), 7, restarted.prepare, identity='alpha')
                await poller.poll_once()
                assert len(chat.inputs) == requests
                await restarted.run(OwnerTurn('alpha', 'alpha', f'telegram:{index}', text))
                assert len(chat.inputs) == requests
            cases = await chat.service.store.list_cases('alpha')
            assert len(cases) == 1
            current = await chat.service.store.get_case('alpha', cases[0].id)
            assert current and current.revision == 2 and current.facts['refund_amount'] == 'INR 2,700'
            drafts = await chat.service.store.list_drafts('alpha', cases[0].id)
            assert len(drafts) == 1 and '2,700' in drafts[0].body
            fail_send = False
            chat.action = {'action': 'list_drafts'}
            updates[:] = [{'update_id': 4, 'message': {'from': {'id': 7}, 'chat': {'id': 7, 'type': 'private'}, 'text': 'List drafts'}}]
            await poller.poll_once()
            assert len(sent) == 1 and 'Version 1' in sent[0]
    asyncio.run(run())


class AcceptanceArtifacts:
    def __init__(self, directory: Path) -> None:
        self.run = directory / 'run.json'
        self.reviews = directory / 'reviews.json'
        self.deterministic = directory / 'deterministic.json'
        self.live = directory / 'live.json'
        junit = directory / 'pytest.xml'
        rows = []
        for nodes in DeterministicEvidence.TESTS.values():
            for node in nodes:
                file, name = node.split('::', 1)
                rows.append(f'<testcase classname="{file.removesuffix(".py").replace("/", ".")}" name="{name}"/>')
        junit.write_text('<testsuites><testsuite>' + ''.join(rows) + '</testsuite></testsuites>')
        fingerprints = AcceptanceFingerprint.current()
        self.deterministic.write_text(json.dumps({'version': 1, 'fingerprints': fingerprints,
            'artifact': {'path': str(junit), 'sha256': AcceptanceFingerprint.file(junit)}}))
        output_rows = []
        reviews = {}
        for case in CASES:
            answer = 'Observed synthetic response ' + case.id
            output = {'scenario_id': case.id, 'expected': case.expected, 'answers': [answer], 'checks': {'fixture_integrity': True, **{key: True for key in (*REQUIRED_CHECKS.get(case.id, ()), *DeterministicEvidence.TESTS.get(case.id, ())) }},
                      'mode': 'deterministic-evidence' if case.family == 'deterministic' else 'real-model-fixed-http-synthetic'}
            if case.id in DeterministicEvidence.TESTS:
                output['deterministic_evidence_sha256'] = AcceptanceFingerprint.file(self.deterministic)
            digest = AcceptanceFingerprint.digest(output)
            output_rows.append({'scenario_id': case.id, 'output': output, 'output_sha256': digest, 'trace_id': 'a' * 32})
            if case.family != 'deterministic':
                reviews[case.id] = {'output_sha256': digest, 'reviewer': 'independent test reviewer', 'criteria': {
                    criterion: {'passed': True, 'excerpt': answer, 'reason': 'Checked against the specified fixture truth.'}
                    for criterion in REVIEW_CRITERIA}}
        self.run.write_text(json.dumps({'version': 1, 'fingerprints': fingerprints, 'outputs': output_rows, 'url': 'synthetic'}))
        self.reviews.write_text(json.dumps({'reviews': reviews}))
        artifact = directory / 'live-observations.json'
        artifact.write_text('{"synthetic_test_artifact":true}')
        self.live.write_text(json.dumps({'version': 1, 'fingerprints': fingerprints, 'checks': {
            key: {'passed': True, 'artifacts': [{'path': str(artifact), 'sha256': AcceptanceFingerprint.file(artifact)}]}
            for key in LIVE_CHECKS}}))

    def finalize(self) -> dict[str, Any]:
        return AcceptanceGate().finalize(self.run, self.reviews, self.deterministic, self.live)


def test_finalization_reuses_exact_outputs_and_complete_supported_reviews(tmp_path: Path) -> None:
    artifacts = AcceptanceArtifacts(tmp_path)
    report = artifacts.finalize()
    assert report['passed'] is True
    assert len(report['outputs']) == 20


@pytest.mark.parametrize('failure', ['missing_review', 'changed_output', 'fabrication', 'wrong_source', 'lost_artifact', 'false_check', 'truncated_checks'])
def test_finalization_fails_closed_on_incomplete_or_stale_evidence(tmp_path: Path, failure: str) -> None:
    artifacts = AcceptanceArtifacts(tmp_path)
    run = json.loads(artifacts.run.read_text())
    reviews = json.loads(artifacts.reviews.read_text())
    if failure == 'missing_review':
        del reviews['reviews']['G01']
    elif failure == 'changed_output':
        run['outputs'][0]['output']['answers'] = ['Different output']
    elif failure == 'fabrication':
        reviews['reviews']['G01']['criteria']['zero_fabrication']['passed'] = False
    elif failure == 'wrong_source':
        run['fingerprints']['runtime_sha256'] = 'wrong'
    elif failure == 'lost_artifact':
        (tmp_path / 'live-observations.json').unlink()
    elif failure == 'truncated_checks':
        run['outputs'][0]['output']['checks'] = {'fixture_integrity': True}
        run['outputs'][0]['output_sha256'] = AcceptanceFingerprint.digest(run['outputs'][0]['output'])
    else:
        run['outputs'][0]['output']['checks']['fixture_integrity'] = False
        run['outputs'][0]['output_sha256'] = AcceptanceFingerprint.digest(run['outputs'][0]['output'])
    artifacts.run.write_text(json.dumps(run))
    artifacts.reviews.write_text(json.dumps(reviews))
    with pytest.raises((ValueError, OSError)):
        artifacts.finalize()


def test_deterministic_import_rejects_skips_and_missing_required_variants(tmp_path: Path) -> None:
    artifacts = AcceptanceArtifacts(tmp_path)
    junit = tmp_path / 'pytest.xml'
    junit.write_text('<testsuites><testsuite><testcase classname="tests.grievances.test_drafts" name="unrelated"><skipped/></testcase></testsuite></testsuites>')
    document = json.loads(artifacts.deterministic.read_text())
    document['artifact']['sha256'] = AcceptanceFingerprint.file(junit)
    artifacts.deterministic.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='skipped'):
        DeterministicEvidence.load(artifacts.deterministic)
    junit.write_text('<testsuites><testsuite/></testsuites>')
    document['artifact']['sha256'] = AcceptanceFingerprint.file(junit)
    artifacts.deterministic.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='missing'):
        DeterministicEvidence.load(artifacts.deterministic)


@pytest.mark.parametrize('document', [None, [], {'fingerprints': []}, {'fingerprints': {}, 'artifact': []}])
def test_deterministic_import_rejects_malformed_manifest_with_value_error(tmp_path: Path, document: Any) -> None:
    path = tmp_path / 'malformed.json'
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='object|fingerprints|artifact|hashes'):
        DeterministicEvidence.load(path)

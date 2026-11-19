"""One finite acceptance experiment and a separate evidence-bound final gate."""

import json
import logging
from pathlib import Path
from typing import Any

from django.conf import settings
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem

from core.tracing import tracing
from grievances.acceptance_catalog import CASES, CASE_BY_ID, LIVE_CHECKS, REQUIRED_CHECKS, REVIEW_CRITERIA
from grievances.acceptance_evidence import AcceptanceFingerprint, DeterministicEvidence
from grievances.acceptance_scenarios import AcceptanceScenarios
from grievances.deletion_evaluation import DeletionScenario
from grievances.draft_evaluation import DraftScenario
from grievances.evaluation import IntakeScenario
from grievances.policy_evaluation import PolicyScenario
from grievances.progress_evaluation import ProgressScenario
from subscriptions.constants import DEFAULT_MODEL
from subscriptions.service import SubscriptionService


logger = logging.getLogger(__name__)


class AcceptanceExperiment:
    def __init__(self, evidence: Path, scenario: str | None = None) -> None:
        if scenario is not None and scenario not in CASE_BY_ID:
            raise ValueError('Unknown acceptance scenario.')
        self.evidence_path = evidence
        self.evidence = DeterministicEvidence.load(evidence)
        self.cases = tuple(case for case in CASES if scenario is None or case.id == scenario)

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Expected hosted dataset item.')
        case = CASE_BY_ID[item.input['scenario_id']]
        logger.info('Acceptance scenario started: %s', case.id)
        if case.family == 'deterministic':
            result: dict[str, Any] = {'checks': self.evidence[case.id], 'mode': 'deterministic-evidence', 'answers': []}
        else:
            async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as model:
                if case.family == 'intake':
                    result = await IntakeScenario(model).run(case.scenario)
                elif case.family == 'draft':
                    result = await DraftScenario(model).run(case.scenario)
                elif case.family == 'policy':
                    result = await PolicyScenario(model).run(case.scenario)
                elif case.family == 'deletion':
                    result = await DeletionScenario(model).run(case.scenario)
                elif case.family == 'isolation':
                    result = await AcceptanceScenarios(model).isolation()
                elif case.scenario == 'outcome_variants':
                    result = await AcceptanceScenarios(model).outcomes()
                else:
                    result = await ProgressScenario(model).run(case.scenario)
            result['mode'] = 'real-model-fixed-http-synthetic'
        if case.id == 'G16':
            result['checks'].update(self.evidence['G16'])
        if case.id in self.evidence:
            result['deterministic_evidence_sha256'] = AcceptanceFingerprint.file(self.evidence_path)
        result['scenario_id'] = case.id
        result['expected'] = case.expected
        result['rubric'] = case.rubric
        logger.info('Acceptance scenario completed: %s checks_passed=%s', case.id, self.checks_pass(result))
        return result

    @staticmethod
    def checks_pass(output: dict[str, Any]) -> bool:
        identifier = output.get('scenario_id', '')
        required = (*REQUIRED_CHECKS.get(identifier, ()), *DeterministicEvidence.TESTS.get(identifier, ()))
        case = CASE_BY_ID.get(identifier)
        if case is None or output.get('expected') != case.expected:
            return False
        mode = 'deterministic-evidence' if case.family == 'deterministic' else 'real-model-fixed-http-synthetic'
        if output.get('mode') != mode:
            return False
        checks = output.get('checks', {})
        if not isinstance(checks, dict):
            return False
        return bool(required) and all(checks.get(key) is True for key in required) and all(value is True for value in checks.values())

    @staticmethod
    def evaluate(*, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        checks = output.get('checks', {})
        scores = [Evaluation(name='state_integrity', value=int(AcceptanceExperiment.checks_pass(output)),
                             data_type='BOOLEAN', comment='Coarse observable integrity checks; not semantic correctness.')]
        for category, words in {'privacy_isolation': ('isolation', 'excluded', 'private', 'disclosure', 'other_owner', 'public_boundary'),
                                'source_grounding': ('source', 'evidence', 'historic', 'backdated', 'conflict_visible', 'impostor')}.items():
            relevant = {key: value for key, value in checks.items() if any(word in key for word in words)}
            if relevant:
                scores.append(Evaluation(name=category, value=int(all(value is True for value in relevant.values())), data_type='BOOLEAN',
                    comment='Only listed observable checks; absent categories are unscored.'))
        scores.append(Evaluation(name='semantic_review', value='not_applicable' if output['mode'] == 'deterministic-evidence' else 'pending', data_type='CATEGORICAL'))
        return scores

    def run(self) -> dict[str, Any]:
        fingerprint = AcceptanceFingerprint.current()
        provenance = AcceptanceFingerprint.provenance()
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY, base_url=settings.LANGFUSE_BASE_URL)
        name = 'grievance-mvp-acceptance-v1'
        try:
            client.create_dataset(name=name, description='Approved G01–G20 finite synthetic acceptance; semantic review is independent.')
            for case in CASES:
                client.create_dataset_item(dataset_name=name, id=name + '-' + case.id,
                    input={'scenario_id': case.id, 'scenario': case.scenario, 'family': case.family},
                    expected_output=case.expected, metadata={'fixture_version': 1, 'synthetic': True, 'rubric': case.rubric,
                        'semantic_review_required': case.family != 'deterministic'})
            dataset = client.get_dataset(name)
            dataset.items = [item for item in dataset.items if item.input.get('scenario_id') in {case.id for case in self.cases}]
            result = dataset.run_experiment(name='grievance-mvp-acceptance', task=self.task, evaluators=[self.evaluate], max_concurrency=1,
                metadata={**fingerprint, **provenance, 'model': DEFAULT_MODEL, 'settings': 'provider default reasoning; application constants',
                          'mode': 'real-model plus separately recorded deterministic evidence'})
            outputs = []
            for item in result.item_results:
                if isinstance(item.output, dict):
                    output = item.output
                elif isinstance(item.item, DatasetItem):
                    case = CASE_BY_ID[item.item.input['scenario_id']]
                    output = {'scenario_id': case.id, 'expected': case.expected, 'checks': {'execution_completed': False},
                              'mode': 'deterministic-evidence' if case.family == 'deterministic' else 'real-model-fixed-http-synthetic',
                              'error': 'Scenario execution failed; inspect the native experiment error.'}
                else:
                    continue
                outputs.append({'scenario_id': output['scenario_id'], 'output': output,
                                'output_sha256': AcceptanceFingerprint.digest(output), 'trace_id': item.trace_id})
            stable = fingerprint == AcceptanceFingerprint.current()
            return {'version': 1, **provenance, 'url': str(result.experiment_url), 'fingerprints': fingerprint, 'model': DEFAULT_MODEL,
                    'outputs': outputs, 'checks_passed': stable and len(outputs) == len(self.cases) and all(
                        self.checks_pass(row['output']) for row in outputs),
                    'passed': False, 'source_stable': stable, 'status': 'independent_review_and_live_evidence_required'}
        finally:
            tracing.flush()
            client.flush()


class AcceptanceGate:
    @staticmethod
    def mapping(value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError('Expected an object for ' + label + '.')
        return value

    @classmethod
    def review_material(cls, output: dict[str, Any]) -> dict[str, Any]:
        material: dict[str, Any] = {key: output[key] for key in ('answers', 'drafts', 'cases', 'research', 'progress', 'deletion_response', 'replay_response', 'remaining_case', 'independent_preferences', 'post_delete_response') if key in output}
        if 'variants' in output:
            material['variants'] = {key: cls.review_material(value) for key, value in output['variants'].items()}
        return material

    def finalize(self, run_path: Path, reviews_path: Path, deterministic_path: Path, live_path: Path) -> dict[str, Any]:
        run = self.mapping(json.loads(run_path.read_text()), 'acceptance run')
        rows = run.get('outputs', [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or len(rows) != 20 or {row.get('scenario_id') for row in rows} != set(CASE_BY_ID):
            raise ValueError('Final acceptance requires exactly 20 distinct scenario outputs.')
        AcceptanceFingerprint.require(self.mapping(run.get('fingerprints', {}), 'run fingerprints'))
        deterministic = DeterministicEvidence.load(deterministic_path)
        reviews = self.mapping(json.loads(reviews_path.read_text()), 'reviews')
        live = self.mapping(json.loads(live_path.read_text()), 'live evidence')
        AcceptanceFingerprint.require(self.mapping(live.get('fingerprints', {}), 'live fingerprints'), live=True)
        for key in LIVE_CHECKS:
            item = self.mapping(self.mapping(live.get('checks', {}), 'live checks').get(key, {}), key)
            if item.get('passed') is not True or not isinstance(item.get('artifacts'), list) or not item['artifacts']:
                raise ValueError('Missing live evidence: ' + key)
            for value in item['artifacts']:
                artifact = self.mapping(value, 'live artifact')
                if not isinstance(artifact.get('path'), str) or not isinstance(artifact.get('sha256'), str):
                    raise ValueError('Live artifacts require a path and checksum.')
                if AcceptanceFingerprint.file(Path(artifact['path'])) != artifact['sha256']:
                    raise ValueError('Live evidence checksum mismatch: ' + key)
        for row in rows:
            identifier = row['scenario_id']
            output = self.mapping(row.get('output'), 'scenario output')
            if row['output_sha256'] != AcceptanceFingerprint.digest(output):
                raise ValueError('Output checksum mismatch: ' + identifier)
            if output.get('scenario_id') != identifier:
                raise ValueError('Scenario output identity mismatch: ' + identifier)
            if not AcceptanceExperiment.checks_pass(output):
                raise ValueError('Observable checks failed: ' + identifier)
            if identifier in deterministic:
                if (output.get('deterministic_evidence_sha256') != AcceptanceFingerprint.file(deterministic_path)
                        or any(output['checks'].get(key) is not value for key, value in deterministic[identifier].items())):
                    raise ValueError('Deterministic output is not bound to the imported evidence: ' + identifier)
            if CASE_BY_ID[identifier].family == 'deterministic':
                continue
            if not isinstance(row.get('trace_id'), str) or not row['trace_id']:
                raise ValueError('Missing native trace linkage: ' + identifier)
            review = self.mapping(self.mapping(reviews.get('reviews', {}), 'scenario reviews').get(identifier, {}), identifier)
            if review.get('output_sha256') != row['output_sha256'] or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
                raise ValueError('Missing output-bound independent review: ' + identifier)
            content = json.dumps(self.review_material(output), ensure_ascii=False)
            for criterion in REVIEW_CRITERIA:
                judgment = self.mapping(self.mapping(review.get('criteria', {}), 'review criteria').get(criterion, {}), criterion)
                if (judgment.get('passed') is not True or not isinstance(judgment.get('reason'), str) or not judgment['reason'].strip()
                        or not isinstance(judgment.get('excerpt'), str) or not judgment['excerpt'].strip()
                        or judgment['excerpt'] not in content):
                    raise ValueError('Missing or failed supported review: ' + identifier + '/' + criterion)
        return {**run, 'passed': True, 'status': 'finite_fixture_acceptance_passed',
                'review_sha256': AcceptanceFingerprint.file(reviews_path), 'live_evidence_sha256': AcceptanceFingerprint.file(live_path),
                'deterministic_evidence_sha256': AcceptanceFingerprint.file(deterministic_path)}


    def publish_review_scores(self, report: dict[str, Any], reviews_path: Path) -> None:
        reviews = json.loads(reviews_path.read_text())['reviews']
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        try:
            for row in report['outputs']:
                identifier = row['scenario_id']
                if CASE_BY_ID[identifier].family == 'deterministic':
                    continue
                review = reviews[identifier]
                for criterion in REVIEW_CRITERIA:
                    judgment = review['criteria'][criterion]
                    score_id = AcceptanceFingerprint.digest([report['url'], identifier, criterion, row['output_sha256']])[:32]
                    client.create_score(name='review_' + criterion, value=1, data_type='BOOLEAN', score_id=score_id,
                        trace_id=row['trace_id'], comment=json.dumps({'reviewer': review['reviewer'], **judgment}, ensure_ascii=False),
                        metadata={'output_sha256': row['output_sha256'], 'scenario_id': identifier, 'review_type': 'independent artifact review'})
        finally:
            client.flush()

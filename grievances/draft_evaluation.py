"""Real-model factual drafting experiments using fictional case facts."""

from dataclasses import asdict
import hashlib
from pathlib import Path
from time import monotonic
from typing import Any

from django.conf import settings
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem
from pydantic_ai.models import Model

from conversations.evaluation import ConversationEvaluationReport
from core.tracing import tracing
from grievances.evaluation import IntakeScenario
from subscriptions.constants import DEFAULT_MODEL
from subscriptions.service import SubscriptionService


class DraftScenario:
    def __init__(self, model: Model) -> None:
        self.intake = IntakeScenario(model)

    async def run(self, name: str) -> dict[str, Any]:
        scenario = self.intake
        message = scenario.COMPLETE
        if name == 'incomplete_draft':
            message = ('Create a refund case called Missing kettle. Company: Lumen Home. '
                       'My kettle arrived broken. I want a refund.')
        elif name == 'money_back_synonym':
            message = message.replace('Create a refund case', 'Create a grievance case').replace('I want a refund of INR 2,400.', 'I want my money back: INR 2,400.')
        elif name == 'remedy_components':
            message = message.replace('I want a refund of INR 2,400.', 'I want a refund of INR 2,400 within 14 days, including INR 100 delivery charges in that total.')
        elif name == 'uncertain_evidence':
            message = message.replace('I have a receipt and video, but have not uploaded either.',
                                      'I may have a receipt; I have not checked. I do not have a video.')
        await scenario.turn(message)
        cases = await scenario.service().store.list_cases(scenario.owner)
        scenario.checks['one_case_created'] = len(cases) == 1
        if len(cases) != 1:
            return scenario.result(name, [])
        case_id = cases[0].id
        await scenario.turn('Draft a factual refund email for my selected case.', delivery='draft-one')
        first_versions = await scenario.service().store.list_drafts(scenario.owner, case_id)
        scenario.checks['one_draft_saved'] = len(first_versions) == 1
        if not first_versions:
            return scenario.result(name, await scenario.snapshots())
        first = first_versions[0]
        original_body = first.body
        scenario.checks['subject_and_body_present'] = bool(first.subject.strip() and first.body.strip())
        if name == 'incomplete_draft':
            scenario.checks['incomplete_not_current'] = bool(first.missing_fields) and not first.current
            scenario.checks['missing_amount_not_invented'] = not any(value in first.body for value in ('2,400', '2400', '3,100', '3100', '2026-11-02'))
        else:
            scenario.checks['complete_current'] = first.current and not first.outdated and not first.missing_fields
            scenario.checks['supplied_amount_and_reference'] = '2400' in first.body.replace(',', '') and 'ORDER-ALPHA-Q7' in first.body
        if name == 'correction_versions':
            await scenario.turn('Draft a factual refund email for my selected case.', delivery='draft-one')
            scenario.checks['draft_replay_idempotent'] = len(await scenario.service().store.list_drafts(scenario.owner, case_id)) == 1
            await scenario.turn('Correction: I paid INR 2,700 and request a refund of INR 2,700. Update both amounts.')
            await scenario.turn('Generate a new factual complaint draft using my corrected facts.')
            versions = await scenario.service().store.list_drafts(scenario.owner, case_id)
            scenario.checks['immutable_versions'] = len(versions) == 2 and versions[0].body == original_body
            scenario.checks['old_outdated_new_current'] = len(versions) == 2 and versions[0].outdated and not versions[0].current and versions[1].current
            scenario.checks['current_amount_corrected'] = len(versions) == 2 and '2700' in versions[1].body.replace(',', '') and '2400' not in versions[1].body.replace(',', '')
            await scenario.turn('List my saved draft versions.')
            await scenario.turn('Read draft version 1 for my selected case.')
        elif name == 'remedy_components':
            scenario.checks['requested_deadline_and_component_preserved'] = '14 days' in first.body and 'INR 100' in first.body
        elif name == 'uncertain_evidence':
            scenario.checks['uncertainty_preserved'] = 'may have a receipt' in first.body and 'do not have a video' in first.body
        elif name == 'case_isolation':
            await scenario.turn('Create another refund case called Cobalt headphones. Company: Cobalt Audio. '
                                'My headphones arrived cracked. I paid INR 3,100 on 2026-11-09 and request a refund of INR 3,100. '
                                'Order reference: ORDER-BETA-R9.', forbidden=('ORDER-ALPHA-Q7',))
            await scenario.turn('Draft a refund email for my selected case.', forbidden=('ORDER-ALPHA-Q7',))
            all_cases = await scenario.service().store.list_cases(scenario.owner)
            other = next((case for case in all_cases if case.id != case_id), None)
            other_versions = await scenario.service().store.list_drafts(scenario.owner, other.id) if other else []
            scenario.checks['other_case_draft_isolated'] = len(other_versions) == 1 and 'ORDER-BETA-R9' in other_versions[0].body and 'ORDER-ALPHA-Q7' not in other_versions[0].body
        scenario.checks['no_public_research'] = not scenario.public_messages
        final_cases = await scenario.snapshots()
        scenario.checks['draft_did_not_resolve_case'] = all(case['status'] == 'open' for case in final_cases)
        result = scenario.result(name, final_cases)
        result['drafts'] = {case['id']: [asdict(draft) for draft in await scenario.service().store.list_drafts(scenario.owner, case['id'])]
                            for case in final_cases}
        return result


class DraftExperiment:
    CASES = ('correction_versions', 'incomplete_draft', 'uncertain_evidence', 'case_isolation', 'remedy_components', 'money_back_synonym')

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Expected hosted dataset item.')
        start = monotonic()
        async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as model:
            result = await DraftScenario(model).run(item.input['scenario'])
        result['elapsed_seconds'] = monotonic() - start
        return result

    def evaluate(self, *, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [Evaluation(name='factual_draft_integrity_pass', value=int(all(output['checks'].values())), data_type='BOOLEAN',
            comment='Finite version, amount and isolation checks. Manually review full draft usefulness, evidence attribution and unsupported claims.')]

    def run(self) -> ConversationEvaluationReport:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        name = 'grievance-factual-drafts-v1'
        prompts = Path(__file__).with_name('prompts.py').read_bytes()
        try:
            client.create_dataset(name=name, description='Fictional factual complaints: corrections/versions, unknown amounts, uncertain evidence and case isolation.')
            for case in self.CASES:
                client.create_dataset_item(dataset_name=name, id=f'{name}-{case}', input={'scenario': case},
                    expected_output={'all_integrity_checks': True}, metadata={'synthetic': True, 'manual_review_required': True})
            result = client.get_dataset(name).run_experiment(name='factual-drafts', task=self.task,
                evaluators=[self.evaluate], max_concurrency=1, metadata={'mode': 'real-model', 'model': DEFAULT_MODEL,
                    'reasoning': 'provider default', 'prompt_sha256': hashlib.sha256(prompts).hexdigest(),
                    'fixture_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
            outputs = [item.output for item in result.item_results if isinstance(item.output, dict)]
            passed = len(outputs) == len(self.CASES) and all(item.evaluations and item.evaluations[0].value == 1 for item in result.item_results)
            return ConversationEvaluationReport(str(result.experiment_url), outputs, passed)
        finally:
            tracing.flush()
            client.flush()

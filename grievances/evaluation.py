"""Synthetic real-model intake experiments at the normalized application turn seam."""

from dataclasses import asdict
import json
from time import monotonic
from typing import Any
from uuid import uuid4

from django.conf import settings
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem
from opentelemetry.context import Context, attach, detach
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from conversations.evaluation import ConversationEvaluationReport
from core.tracing import tracing
from core.turns import OwnerTurn
from grievances.service import ApplicationService
from subscriptions.service import SubscriptionService


class ObservedModel(WrapperModel):
    def __init__(self, model: Model) -> None:
        super().__init__(model)
        self.inputs: list[str] = []
        self.proposals: list[dict[str, Any]] = []

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        self.inputs.append(str(messages) + str(model_request_parameters))
        response = await self.wrapped.request(messages, model_settings, model_request_parameters)
        self.proposals.extend({'tool': part.tool_name, 'arguments': part.args_as_dict()}
                              for part in response.parts if isinstance(part, ToolCallPart))
        return response


class IntakeScenario:
    COMPLETE = ('Create a refund case called Lumen kettle. Company: Lumen Home. Product: kettle. '
                'Purchased on 2026-11-02 and delivered on 2026-11-05. I paid INR 2,400. '
                'It did not turn on when first tested on delivery day. I want a refund of INR 2,400. '
                'Order reference: ORDER-ALPHA-Q7. I have a receipt and video, but have not uploaded either.')

    def __init__(self, model: Model) -> None:
        self.model = ObservedModel(model)
        self.owner = f'evaluation-case-{uuid4().hex}'
        self.conversation = self.owner
        self.public_messages: list[str] = []
        self.answers: list[str] = []
        self.checks: dict[str, bool] = {}
        self.deliveries: set[str] = set()
        self.proposals: list[list[dict[str, Any]]] = []

    async def public(self, message: str) -> str:
        self.public_messages.append(message)
        return 'Public research boundary reached.'

    def service(self) -> ApplicationService:
        return ApplicationService(self.model, self.public)

    async def turn(self, message: str, *, delivery: str | None = None,
                   forbidden: tuple[str, ...] = ()) -> str:
        self.model.inputs.clear()
        self.model.proposals.clear()
        delivery_id = delivery or uuid4().hex
        replay = delivery_id in self.deliveries
        token = attach(Context())
        try:
            answer = await self.service().run(OwnerTurn(self.owner, self.conversation, delivery_id, message))
        finally:
            detach(token)
        self.answers.append(answer)
        self.proposals.append(list(self.model.proposals))
        self.deliveries.add(delivery_id)
        self.checks[f'model_input_observed_{len(self.answers)}'] = bool(self.model.inputs) or replay
        exposed = '\n'.join([*self.model.inputs, answer])
        self.checks[f'context_isolation_{len(self.answers)}'] = all(marker not in exposed for marker in forbidden)
        return answer

    async def run(self, name: str) -> dict[str, Any]:
        if name == 'missing_facts':
            await self.turn('Create a refund case called Missing kettle. Company: Lumen Home. My kettle arrived broken. I want a refund.')
        else:
            await self.turn(self.COMPLETE, delivery='create-alpha')
        cases = await self.service().store.list_cases(self.owner)
        self.checks['one_case_created'] = len(cases) == 1
        if len(cases) != 1:
            return self.result(name, [])
        first = await self.service().store.get_case(self.owner, cases[0].id)
        if first is None:
            self.checks['case_readable'] = False
            return self.result(name, [])
        if name == 'complete_intake':
            initial = json.dumps(first.facts).casefold().replace(',', '')
            self.checks['supplied_facts_preserved'] = all(word in initial for word in (
                'lumen', 'kettle', '2400', '2026-11-02', '2026-11-05', 'order-alpha-q7', 'refund'))
            self.checks['case_open'] = first.status == 'open'
            await self.turn(self.COMPLETE, delivery='create-alpha')
            repeated = await self.service().store.list_cases(self.owner)
            self.checks['duplicate_delivery_idempotent'] = len(repeated) == 1
        elif name == 'missing_facts':
            self.checks['unknowns_not_invented'] = not any(
                value for key, value in first.facts.items() if 'amount' in key or 'date' in key)
        elif name == 'conflict_and_correction':
            await self.turn('The amount I paid was INR 2,700.')
            conflicted = await self.service().store.get_case(self.owner, first.id)
            self.checks['conflict_did_not_overwrite'] = bool(conflicted and conflicted.facts == first.facts and conflicted.revision == first.revision)
            await self.turn('Correction: I paid INR 2,700, not INR 2,400. Update the amount paid only.', delivery='correct-alpha')
            corrected = await self.service().store.get_case(self.owner, first.id)
            self.checks['explicit_correction_saved'] = bool(corrected and corrected.revision > first.revision and '2700' in corrected.facts.get('amount_paid', '').replace(',', '') and corrected.facts.get('refund_amount') == first.facts.get('refund_amount'))
            await self.turn('Correction: I paid INR 2,700, not INR 2,400. Update the amount paid only.', delivery='correct-alpha')
            repeated_view = await self.service().store.get_case(self.owner, first.id)
            self.checks['correction_replay_idempotent'] = bool(corrected and repeated_view and corrected.revision == repeated_view.revision)
        elif name == 'conflicting_reference':
            await self.turn('For Cobalt Audio, update the order reference to ORDER-BETA-R9.')
            after = await self.service().store.get_case(self.owner, first.id)
            self.checks['conflicting_reference_preserves_facts'] = bool(after and after.facts == first.facts and after.revision == first.revision)
        elif name in ('case_isolation', 'ambiguous_target'):
            await self.turn('Create another refund case called Cobalt headphones. Company: Cobalt Audio. '
                            'My headphones arrived cracked. I paid INR 3,100 and want a refund. '
                            'Order reference: ORDER-BETA-R9.', forbidden=('ORDER-ALPHA-Q7',))
            cases = await self.service().store.list_cases(self.owner)
            self.checks['two_cases_created'] = len(cases) == 2
            await self.turn('Select the Lumen kettle case.', forbidden=('ORDER-BETA-R9',))
            await self.turn('Show the facts for my selected case.', forbidden=('ORDER-BETA-R9',))
            await self.turn('Return to general public research mode.')
            if name == 'ambiguous_target':
                before = await self.snapshots()
                await self.turn('Add that it arrived broken to that order.')
                after_snapshots = await self.snapshots()
                self.checks['ambiguous_target_unchanged'] = before == after_snapshots
                self.checks['ambiguous_private_request_not_public'] = not self.public_messages
            else:
                await self.turn('Find the official Python documentation for asyncio.timeout.', forbidden=('ORDER-ALPHA-Q7', 'ORDER-BETA-R9'))
                self.checks['public_route_used'] = len(self.public_messages) == 1
                self.checks['public_payload_isolated'] = all(marker not in str(self.public_messages) for marker in ('ORDER-ALPHA-Q7', 'ORDER-BETA-R9'))
        if name not in ('case_isolation',):
            self.checks['private_turns_never_public'] = not self.public_messages
        return self.result(name, await self.snapshots())

    async def snapshots(self) -> list[dict[str, Any]]:
        final = []
        for case in await self.service().store.list_cases(self.owner):
            view = await self.service().store.get_case(self.owner, case.id)
            if view:
                final.append(asdict(view))
        return final

    def result(self, name: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
        return {'scenario': name, 'owner': self.owner, 'checks': self.checks,
                'answers': self.answers, 'proposals': self.proposals, 'cases': cases}


class IntakeExperiment:
    CASES = ('complete_intake', 'missing_facts', 'conflict_and_correction', 'case_isolation', 'ambiguous_target', 'conflicting_reference')

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Expected hosted dataset item.')
        start = monotonic()
        async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as model:
            result = await IntakeScenario(model).run(item.input['scenario'])
        result['elapsed_seconds'] = monotonic() - start
        return result

    def evaluate(self, *, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [Evaluation(name='case_intake_integrity_pass', value=int(all(output['checks'].values())), data_type='BOOLEAN',
                           comment='Finite synthetic state, routing and context checks; manually review extracted facts and clarification quality.')]

    def run(self) -> ConversationEvaluationReport:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        name = 'grievance-intake-v1'
        try:
            client.create_dataset(name=name, description='Synthetic case intake, missing facts, corrections, isolation and ambiguity. No live web or real case data.')
            for case in self.CASES:
                client.create_dataset_item(dataset_name=name, id=f'{name}-{case}', input={'scenario': case},
                    expected_output={'all_integrity_checks': True}, metadata={'synthetic': True, 'manual_review_required': True})
            result = client.get_dataset(name).run_experiment(name='case-intake', task=self.task,
                evaluators=[self.evaluate], max_concurrency=1, metadata={'mode': 'real-model', 'scope': 'intake only'})
            outputs = [item.output for item in result.item_results if isinstance(item.output, dict)]
            passed = len(outputs) == len(self.CASES) and all(item.evaluations and item.evaluations[0].value == 1 for item in result.item_results)
            return ConversationEvaluationReport(str(result.experiment_url), outputs, passed)
        finally:
            tracing.flush()
            client.flush()

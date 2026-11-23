"""Synthetic real-model checks through the application router used by Telegram."""

from typing import Any

from django.conf import settings
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem

from conversations.evaluation import ConversationEvaluationReport
from core.tracing import tracing
from grievances.evaluation import IntakeScenario
from preferences.store import PreferenceStore
from subscriptions.service import SubscriptionService


class RoutingScenario(IntakeScenario):
    async def run(self, name: str) -> dict[str, Any]:
        if name == 'selected_case':
            await self.turn(self.COMPLETE)
        before = await self.snapshots()
        selection = await self.service().store.selection(self.owner, self.conversation)
        if name == 'preferences':
            await self.turn('Remember that I prefer concise answers.')
            self.checks['preference_saved'] = bool(await PreferenceStore().list(self.owner))
            await self.turn('What preferences have you saved?')
            self.checks['preference_recalled'] = 'concise' in self.answers[-1].lower()
            await self.turn('Forget my preference for concise answers.')
            self.checks['preference_forgotten'] = not await PreferenceStore().list(self.owner)
        elif name == 'public_research':
            await self.turn('Find the official Python documentation for asyncio.timeout and cite the source.')
            self.checks['public_route_reached'] = len(self.public_messages) == 1
        elif name == 'ambiguous_case':
            await self.turn('Update that order to say it arrived broken.')
            self.checks['clarified_case'] = 'clarify' in self.answers[-1].lower()
        else:
            await self.turn('For this fictional comparison, my two languages are Python and Ruby. Acknowledge without searching.')
            self.checks['acknowledged'] = all(word in self.answers[-1].lower() for word in ('python', 'ruby'))
            await self.turn('Which two languages did I mention?')
            self.checks['followup_after_service_restart'] = all(word in self.answers[-1].lower() for word in ('python', 'ruby'))
            await self.turn('Use our conversation archive to find the two languages I mentioned; cite the original message ID. Do not search the web.')
            self.checks['archive_tool_called'] = any(p['tool'] == 'recall_conversation' for p in self.proposals[-1])
            self.checks['archive_answer'] = all(word in self.answers[-1].lower() for word in ('python', 'ruby'))
        after = await self.snapshots()
        self.checks['case_state_unchanged'] = before == after
        after_selection = await self.service().store.selection(self.owner, self.conversation)
        self.checks['selection_unchanged'] = selection.case_id == after_selection.case_id
        if name != 'public_research':
            self.checks['no_web_route'] = not self.public_messages
        return self.result(name, after)


class RoutingExperiment:
    CASES = ('ordinary_conversation', 'selected_case', 'preferences', 'public_research', 'ambiguous_case')

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Expected hosted dataset item.')
        async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as model:
            return await RoutingScenario(model).run(item.input['scenario'])

    def evaluate(self, *, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [Evaluation(name='application_routing_checks', value=int(all(output['checks'].values())),
            data_type='BOOLEAN', comment='Real application routing, conversation, archive and preference checks. Public web is a controlled boundary. Review actual answers and proposals.')]

    def run(self) -> ConversationEvaluationReport:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        name = 'application-routing-v1'
        try:
            client.create_dataset(name=name, description='Synthetic integrated routing regressions, including ordinary conversation before and after selecting a case.')
            for case in self.CASES:
                client.create_dataset_item(dataset_name=name, id=f'{name}-{case}', input={'scenario': case},
                    expected_output={'all_checks': True}, metadata={'synthetic': True, 'manual_review_required': True})
            result = client.get_dataset(name).run_experiment(name='application-routing', task=self.task,
                evaluators=[self.evaluate], max_concurrency=1)
            outputs = [item.output for item in result.item_results if isinstance(item.output, dict)]
            passed = len(outputs) == len(self.CASES) and all(item.evaluations and item.evaluations[0].value == 1 for item in result.item_results)
            return ConversationEvaluationReport(str(result.experiment_url), outputs, passed)
        finally:
            tracing.flush()
            client.flush()

"""Real-model reply checking against synthetic mail; no connected mailbox access."""
import base64
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from django.conf import settings
import httpx2
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem

from core.tracing import tracing
from gmail.correspondence import Correspondence
from gmail.evaluation import GmailExperiment, SyntheticCredentials, SyntheticGoogle, SyntheticModelRecorder
from gmail.mailbox import Mailbox
from gmail.models import GmailReplyCheckState
from gmail.planner import GmailQueryPlanner
from gmail.service import GmailService
from grievances.models import CaseOutcome, ComplaintDraft
from grievances.service import ApplicationService
from subscriptions.service import SubscriptionService


class GmailReplyExperiment(GmailExperiment):
    reply_body = ''
    checking = False

    def fixture(self, request: httpx2.Request) -> httpx2.Response:
        response = super().fixture(request)
        if self.checking and '/threads/' in request.url.path:
            data = response.json()
            data['messages'].append({'id': 'a2', 'threadId': 'b1', 'labelIds': ['INBOX'], 'payload': {
                'headers': [{'name': 'From', 'value': 'support@example.invalid'},
                            {'name': 'Subject', 'value': 'Re: Lumen order ORDER-Q7'}],
                'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(self.reply_body.encode()).decode()}}})
            return httpx2.Response(200, json=data)
        return response

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Hosted synthetic dataset required')
        owner = 'gmail-reply-evaluation-' + uuid4().hex
        self.checking = False
        self.reply_body = item.input['reply']
        self.queries, self.created = [], []
        with TemporaryDirectory() as directory, httpx2.Client(transport=httpx2.MockTransport(self.fixture)) as http:
            mailbox = Mailbox(GmailService(SyntheticCredentials(), SyntheticGoogle(), Path(directory)), http)
            async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as raw_model:
                model = SyntheticModelRecorder(raw_model)
                model.records = []
                app = ApplicationService(model, correspondence=Correspondence(lambda: nullcontext(mailbox), GmailQueryPlanner(model)))
                await self.turn(app, owner, 'consent', 'I agree to email processing')
                setup = await self.turn(app, owner, 'setup', 'Help me get my Lumen refund.')
                self.checking = True
                first = await self.turn(app, owner, 'check1', 'Did they reply to my refund request? Check my email.')
                second = await self.turn(app, owner, 'check2', 'Check again: are there any new replies?')
        state = await GmailReplyCheckState.objects.filter(mailbox_record__case__owner_id=owner).afirst()
        finding = next((value for value in state.findings if value['message_id'] == 'a2'), None) if state else None
        return {'synthetic': True, 'answers': [setup, first, second], 'model_steps': model.records,
            'checks': {'stored_evidence': bool(finding),
                'classification': bool(finding and finding['classification'] == item.expected_output['classification']),
                'new_message': '1 new received' in first,
                'repeat_deduplicated': 'No newly received' in second,
                'no_drafts': not await ComplaintDraft.objects.filter(case__owner_id=owner).aexists(),
                'not_resolved': not await CaseOutcome.objects.filter(case__owner_id=owner).aexists()}}

    @staticmethod
    def evaluate(*, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [Evaluation(name='gmail_reply_checks', value=int(all(output['checks'].values())), data_type='BOOLEAN',
            comment='Real model with synthetic Gmail; checks natural routing, classification, duplicate replies and no draft/resolution. Manual summary review still required.')]

    def run(self) -> dict[str, Any]:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
            base_url=settings.LANGFUSE_BASE_URL)
        dataset = 'gmail-reply-checks-v1'
        examples = [('substantive', 'We reviewed ORDER-Q7 and approved your refund of INR 2400. It has not been paid yet.'),
            ('acknowledgement', 'This is an automated receipt. We received your request and will respond later. No decision has been made.'),
            ('uncertain', 'See attachment for our answer. The attached document is unavailable in this excerpt.')]
        try:
            client.create_dataset(name=dataset, description='Synthetic reply checking; never accesses connected Gmail.')
            for classification, reply in examples:
                client.create_dataset_item(dataset_name=dataset, id=f'{dataset}-{classification}',
                    input={'reply': reply}, expected_output={'classification': classification})
            result = client.get_dataset(dataset).run_experiment(name='gmail-reply-checks', task=self.task,
                evaluators=[self.evaluate], max_concurrency=1)
            return {'url': str(result.experiment_url), 'outputs': [item.output for item in result.item_results],
                'passed': len(result.item_results) == len(examples) and all(
                    item.evaluations and item.evaluations[0].value == 1 for item in result.item_results)}
        finally:
            tracing.flush()
            client.flush()

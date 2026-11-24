"""Real-model reminder routing and worker checks with synthetic Gmail only."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.utils import timezone
import httpx2
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem
from pydantic_ai.models import Model

from core.tracing import tracing
from core.turns import PreparedReply
from gmail.correspondence import Correspondence
from gmail.evaluation import SyntheticCredentials, SyntheticGoogle, SyntheticModelRecorder
from gmail.mailbox import Mailbox
from gmail.models import CaseReminder, CaseMonitor
from gmail.monitoring import MonitoringWorker
from gmail.planner import GmailQueryPlanner
from gmail.reply_evaluation import GmailReplyExperiment
from gmail.service import GmailService
from grievances.service import ApplicationService
from subscriptions.service import SubscriptionService


class GmailReminderExperiment(GmailReplyExperiment):
    def fixture(self, request: httpx2.Request) -> httpx2.Response:
        response = super().fixture(request)
        data = response.json()
        messages = data['messages'] if '/threads/' in request.url.path else [data]
        for message in messages:
            if message.get('id') == 'a1' and 'payload' in message:
                message['labelIds'] = ['SENT']
                for header in message['payload']['headers']:
                    if header['name'] == 'From':
                        header['value'] = 'fictional@example.invalid'
        return httpx2.Response(200, json=data)

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Hosted synthetic dataset required')
        owner = 'gmail-reminder-evaluation-' + uuid4().hex
        self.checking = False
        self.reply_body = item.input['reply']
        self.queries, self.created = [], []
        notifications: list[str] = []
        async def send(reply: PreparedReply) -> None:
            if await reply.valid():
                notifications.append(await reply.render())
        with TemporaryDirectory() as directory, httpx2.Client(transport=httpx2.MockTransport(self.fixture)) as http:
            mailbox = Mailbox(GmailService(SyntheticCredentials(), SyntheticGoogle(), Path(directory)), http)
            async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as raw_model:
                model = SyntheticModelRecorder(raw_model)
                model.records = []
                @asynccontextmanager
                async def model_session() -> AsyncIterator[Model]:
                    yield model
                app = ApplicationService(model, correspondence=Correspondence(lambda: nullcontext(mailbox), GmailQueryPlanner(model)))
                await self.turn(app, owner, 'consent', 'I agree to email processing')
                setup = await self.turn(app, owner, 'setup', 'Help me get my Lumen refund. Check my emails for the correspondence.')
                automatic = await CaseMonitor.objects.filter(mailbox_record__case__owner_id=owner, active=True).aexists()
                deadline = (timezone.now() + timedelta(days=1)).replace(second=0, microsecond=0)
                answer = await self.turn(app, owner, 'reminder', f'Remind me on {deadline.isoformat()} if they have not substantively replied.')
                reminder = await CaseReminder.objects.filter(monitor__mailbox_record__case__owner_id=owner).afirst()
                if reminder:
                    self.checking = True
                    worker = MonitoringWorker(model_session, lambda: nullcontext(mailbox), send,
                        clock=lambda: deadline + timedelta(minutes=1))
                    await worker.tick(owner)
                    await worker.tick(owner)
                    await reminder.arefresh_from_db()
        return {'synthetic': True, 'answers': [setup, answer], 'notifications': notifications,
            'model_steps': model.records, 'checks': {'automatic_monitor': automatic,
                'deadline': bool(reminder and reminder.due_at == deadline),
                'status': bool(reminder and reminder.status == item.expected_output['status']),
                'no_duplicate_reminder': sum('found no substantive reply' in text for text in notifications) <= 1,
                'no_false_no_reply': item.expected_output['status'] == 'notified' or not any('found no substantive reply' in text for text in notifications)}}

    @staticmethod
    def evaluate(*, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [Evaluation(name='gmail_reminder_checks', value=int(all(output['checks'].values())), data_type='BOOLEAN',
            comment='Real model; synthetic Gmail; automatic monitoring, deadline routing, evidence classification and duplicate delivery checks. Manual prose review required.')]

    def run(self) -> dict[str, Any]:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
            base_url=settings.LANGFUSE_BASE_URL)
        dataset = 'gmail-reminders-v1'
        examples = [('satisfied', 'Your refund is approved. Payment is pending.'),
            ('notified', 'Automated acknowledgement only. No decision has been made.'),
            ('clarify', 'Our decision is in the attachment, which is unavailable in this excerpt.')]
        try:
            client.create_dataset(name=dataset, description='Synthetic deadline and monitoring checks; no real Gmail access.')
            for status, reply in examples:
                client.create_dataset_item(dataset_name=dataset, id=f'{dataset}-{status}',
                    input={'reply': reply}, expected_output={'status': status})
            result = client.get_dataset(dataset).run_experiment(name='gmail-reminders', task=self.task,
                evaluators=[self.evaluate], max_concurrency=1)
            return {'url': str(result.experiment_url), 'outputs': [item.output for item in result.item_results],
                'passed': len(result.item_results) == len(examples) and all(
                    item.evaluations and item.evaluations[0].value == 1 for item in result.item_results)}
        finally:
            tracing.flush()
            client.flush()

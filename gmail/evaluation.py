"""Real-model routing against synthetic Gmail HTTP; never reads the connected inbox."""
from contextlib import nullcontext
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4
import re

from django.conf import settings
from google.oauth2.credentials import Credentials
import httpx2
from langfuse import Evaluation, Langfuse
from langfuse.api import DatasetItem
from langfuse.experiment import ExperimentItem

from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.settings import ModelSettings

from core.tracing import tracing
from core.turns import OwnerTurn
from gmail.client import GoogleClient
from gmail.constants import SCOPES, TOKEN_URL
from gmail.correspondence import Correspondence
from gmail.credentials import Connection
from gmail.mailbox import Mailbox
from gmail.planner import GmailQueryPlanner
from gmail.service import GmailService
from grievances.service import ApplicationService
from subscriptions.service import SubscriptionService


class SyntheticCredentials:
    def __init__(self) -> None:
        self.connection = Connection('fictional@example.invalid', Credentials('synthetic', refresh_token='synthetic',
            token_uri=TOKEN_URL, client_id='fixture', scopes=list(SCOPES),
            expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)))
    def load(self) -> Connection:
        return self.connection
    def save(self, connection: Connection) -> None:
        self.connection = connection
    def disable(self) -> None:
        raise RuntimeError('Evaluation does not disconnect accounts')
    def clear(self) -> None:
        raise RuntimeError('Evaluation does not disconnect accounts')


class SyntheticGoogle(GoogleClient):
    def profile(self, credentials: Credentials) -> str:
        return 'fictional@example.invalid'


class SyntheticModelRecorder(WrapperModel):
    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        result = await super().request(messages, model_settings, model_request_parameters)
        self.records.append({'input': str(messages), 'output': str([part for part in result.parts if isinstance(part, (TextPart, ToolCallPart))])})
        return result

    records: list[dict[str, str]]


class GmailExperiment:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.licence = False

    def fixture(self, request: httpx2.Request) -> httpx2.Response:
        if request.method != 'GET':
            raise AssertionError('Only reading is allowed')
        if request.url.path.endswith('/messages'):
            self.queries.append(request.url.params['q'])
            if self.licence and request.url.params['q'].strip().casefold() == '"licence approval"':
                return httpx2.Response(200, json={'messages': []})
            return httpx2.Response(200, json={'messages': [{'id': 'a1', 'threadId': 'b1'}]})
        message = {'id': 'a1', 'threadId': 'b1', 'snippet': 'Licence application decision' if self.licence else 'Lumen receipt for order ORDER-Q7', 'payload': {
            'headers': [{'name': 'From', 'value': 'support@example.invalid'}, {'name': 'Subject', 'value': 'License application approved' if self.licence else 'Lumen order ORDER-Q7'}],
            'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(b'License application approved.' if self.licence else b'Receipt for order ORDER-Q7. Amount paid INR 2400. Your refund request is under review; it has not been approved.').decode()}}}
        return httpx2.Response(200, json={'id': 'b1', 'messages': [message]} if '/threads/' in request.url.path else message)

    @staticmethod
    async def turn(app: ApplicationService, owner: str, delivery: str, request: str) -> str:
        # Only this mock-mailbox experiment uses readable wrappers; production privacy stays on.
        with tracing.operation('synthetic.gmail.turn', {'fixture': True, 'request': request}, kind='agent') as span:
            answer = await app.run(OwnerTurn(owner, owner, delivery, request))
            tracing.record_output(span, {'fixture': True, 'answer': answer})
            return answer

    async def task(self, *, item: ExperimentItem, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(item, DatasetItem):
            raise ValueError('Hosted synthetic dataset required')
        owner = 'gmail-evaluation-' + uuid4().hex
        answers: list[str] = []
        self.queries = []
        self.licence = item.input.get('topic') == 'licence'
        with TemporaryDirectory() as directory, httpx2.Client(transport=httpx2.MockTransport(self.fixture)) as http:
            mail = Mailbox(GmailService(SyntheticCredentials(), SyntheticGoogle(), Path(directory)), http)
            async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as raw_model:
                model = SyntheticModelRecorder(raw_model)
                model.records = []
                app = ApplicationService(model, correspondence=Correspondence(lambda: nullcontext(mail), GmailQueryPlanner(model)))
                requests = ['I agree to email processing', item.input['search']]
                for index, request in enumerate(requests):
                    answers.append(await self.turn(app, owner, str(index), request))
        return {'synthetic': True, 'answers': answers, 'queries': self.queries, 'model_steps': model.records,
            'checks': {'search_reached': bool(self.queries),
                'bounded_queries': 1 <= len(self.queries) <= 3,
                'natural_reply': bool(answers[-1]) and 'Select Gmail candidate' not in answers[-1],
                'email_evidence_used': ('approv' in answers[-1].lower() if self.licence else '2400' in answers[-1].replace(',', ''))}}

    @staticmethod
    def evaluate(*, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [Evaluation(name='gmail_correspondence_checks', value=int(all(output['checks'].values())), data_type='BOOLEAN',
            comment='Real routing model, synthetic Gmail HTTP. Checks bounded search and natural evidence-based output; manual semantic review remains required.')]

    def run(self) -> dict[str, Any]:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        try:
            dataset = 'gmail-natural-journey-v1'
            client.create_dataset(name=dataset, description='Synthetic mail only; no connected Gmail access.')
            for index, search in enumerate(('Help me get my Lumen refund.', 'I need help getting my money back from Lumen.', 'Help me with my licence approval.')):
                client.create_dataset_item(dataset_name=dataset, id=f'{dataset}-{index}', input={'search': search, 'topic': 'licence' if index == 2 else 'order'},
                    expected_output={'search': True, 'attribution': True})
            result = client.get_dataset(dataset).run_experiment(name='gmail-correspondence', task=self.task,
                evaluators=[self.evaluate], max_concurrency=1)
            return {'url': str(result.experiment_url), 'outputs': [r.output for r in result.item_results],
                'passed': len(result.item_results) == 3 and all(r.evaluations and r.evaluations[0].value == 1 for r in result.item_results)}
        finally:
            tracing.flush()
            client.flush()

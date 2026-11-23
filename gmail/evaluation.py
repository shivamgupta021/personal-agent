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
        message = {'id': 'a1', 'threadId': 'b1', 'snippet': 'Fictional licence decision' if self.licence else 'Fictional Lumen receipt', 'payload': {
            'headers': [{'name': 'From', 'value': 'support@example.invalid'}, {'name': 'Subject', 'value': 'Fictional license application approved' if self.licence else 'Fictional Lumen order'}],
            'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(b'Fictional license application approved.' if self.licence else b'Fictional receipt for order ORDER-Q7. Amount INR 2400.').decode()}}}
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
            async with SubscriptionService(settings.SUBSCRIPTION_STATE_DIR).model() as model:
                app = ApplicationService(model, correspondence=Correspondence(lambda: nullcontext(mail), GmailQueryPlanner(model)))
                requests = [('Create a case called licence approval.' if self.licence else 'Create a case called Lumen kettle. Company: Lumen. Order reference: ORDER-Q7.'),
                    'I agree to email processing', item.input['search']]
                for index, request in enumerate(requests):
                    answers.append(await self.turn(app, owner, str(index), request))
                match = re.search(r'([a-f0-9]{12}) —', answers[-1])
                if match:
                    answers.append(await self.turn(app, owner, 'select', 'Select Gmail candidate ' + match[1]))
                    answers.append(await self.turn(app, owner, 'show', 'Show the selected Gmail correspondence and its source references.'))
        return {'synthetic': True, 'answers': answers, 'queries': self.queries,
            'checks': {'search_reached': bool(self.queries),
                'case_query': (any('licen' in query.casefold() or 'licenc' in query.casefold() for query in self.queries) if self.licence else all(any(word in query for query in self.queries) for word in ('Lumen', 'ORDER-Q7'))),
                'bounded_queries': 1 <= len(self.queries) <= 3,
                'selected': len(answers) == 5 and 'saved for this case' in answers[3],
                'attribution': len(answers) == 5 and 'Message a1' in answers[4] and ('license application approved' if self.licence else 'INR 2400') in answers[4]}}

    @staticmethod
    def evaluate(*, output: dict[str, Any], **kwargs: Any) -> list[Evaluation]:
        return [Evaluation(name='gmail_correspondence_checks', value=int(all(output['checks'].values())), data_type='BOOLEAN',
            comment='Real routing model, synthetic Gmail HTTP. Checks query relevance, selection and source attribution; review outputs for semantic quality.')]

    def run(self) -> dict[str, Any]:
        client = Langfuse(public_key=settings.LANGFUSE_PUBLIC_KEY, secret_key=settings.LANGFUSE_SECRET_KEY,
                          base_url=settings.LANGFUSE_BASE_URL)
        try:
            dataset = 'gmail-correspondence-v1'
            client.create_dataset(name=dataset, description='Synthetic mail only; no connected Gmail access.')
            for index, search in enumerate(('Search my Gmail for this case.', 'Find earlier order emails for the selected case in Gmail.', 'Find emails about my licence approval.')):
                client.create_dataset_item(dataset_name=dataset, id=f'{dataset}-{index}', input={'search': search, 'topic': 'licence' if index == 2 else 'order'},
                    expected_output={'search': True, 'attribution': True})
            result = client.get_dataset(dataset).run_experiment(name='gmail-correspondence', task=self.task,
                evaluators=[self.evaluate], max_concurrency=1)
            return {'url': str(result.experiment_url), 'outputs': [r.output for r in result.item_results],
                'passed': len(result.item_results) == 3 and all(r.evaluations and r.evaluations[0].value == 1 for r in result.item_results)}
        finally:
            tracing.flush()
            client.flush()

"""External boundaries for repeatable evaluation; never falls back to the network."""

import json
from urllib.parse import urldefrag

import httpx2
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo

from research.evaluation.schema import EvaluationCase


class FixtureWeb:
    def __init__(self, case: EvaluationCase) -> None:
        self.case = case
        self.searches = 0
        self.unmatched: list[str] = []

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if request.url.path == '/v2/search':
            index = min(self.searches, len(self.case.search_responses) - 1)
            self.searches += 1
            return httpx2.Response(200, json=self.case.search_responses[index])
        if request.url.path == '/v2/scrape':
            url = urldefrag(body.get('url', ''))[0]
            if url in self.case.pages:
                return httpx2.Response(200, json={
                    'success': True, 'data': {'markdown': self.case.pages[url]},
                })
            self.unmatched.append(url)
        else:
            self.unmatched.append(request.url.path)
        return httpx2.Response(404, json={'success': False})


class ReplayModel:
    def __init__(self, case: EvaluationCase) -> None:
        self.steps = iter(case.replay)

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = next(self.steps)
        if step.tool:
            return ModelResponse(parts=[ToolCallPart(step.tool, step.arguments)])
        return ModelResponse(parts=[TextPart(step.answer)])

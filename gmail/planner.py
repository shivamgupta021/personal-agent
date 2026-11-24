"""A private, tool-free query planner, independently invokable from Gmail execution."""
import json
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.tracing import tracing
from gmail import constants
from gmail.prompts import GMAIL_QUERY_INSTRUCTIONS

Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=constants.MAX_QUERY_CHARS)]


class GmailQueryPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    queries: list[Query] = Field(min_length=1, max_length=constants.PLANNER_MAX_QUERIES)

    @field_validator('queries')
    @classmethod
    def distinct_queries(cls, queries: list[str]) -> list[str]:
        if len({query.casefold() for query in queries}) != len(queries):
            raise ValueError('Queries must be distinct')
        if any(not any(character.isalnum() for character in query) or query in ('in:anywhere', 'in:inbox', 'is:read', 'is:unread') for query in queries):
            raise ValueError('Each query must contain meaningful case search terms')
        return queries


class GmailQueryPlanner:
    def __init__(self, model: Model) -> None:
        self.agent: Agent[None, GmailQueryPlan] = Agent(model, name='gmail_query_planner',
            instructions=GMAIL_QUERY_INSTRUCTIONS, output_type=GmailQueryPlan,
            retries=constants.PLANNER_RETRIES, model_settings={'max_tokens': constants.PLANNER_MAX_OUTPUT_TOKENS})
        self.agent.instrument = tracing.private_instrumentation()

    async def run(self, request: str, label: str, identifiers: dict[str, str],
                  usage: RunUsage | None = None, request_limit: int | None = None) -> GmailQueryPlan:
        limit = (usage.requests if usage else 0) + constants.PLANNER_REQUEST_LIMIT
        if request_limit is not None:
            limit = min(limit, request_limit)
        result = await self.agent.run(json.dumps({'request': request, 'case_label': label, 'identifiers': identifiers}),
            usage=usage, usage_limits=UsageLimits(request_limit=limit), timeout=constants.PLANNER_TIMEOUT_SECONDS)
        # Preserve ungrouped explicit filters as a conjunction outside each alternative.
        # Grouped/OR expressions remain the planner's responsibility; flattening changes meaning.
        filters = [] if re.search(r'\bOR\b|[{}()]', request) else re.findall(
            r'(?<!\S)-?(?:from|to|cc|bcc|subject|after|before|newer_than|older_than|label|in|has|is):(?:"[^"]+"|[^\s]+)', request)
        suffix = ' '.join(value.rstrip('.,;') for value in filters)
        if suffix:
            return GmailQueryPlan(queries=[f'({query}) {suffix}' for query in result.output.queries])
        return result.output

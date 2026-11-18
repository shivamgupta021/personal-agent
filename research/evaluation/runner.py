"""Run the production loop and assess returned evidence separately from its answer."""

import hashlib
import re
from time import perf_counter
from urllib.parse import urldefrag

import httpx2
from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import FunctionModel

from core.errors import user_error_message
from research.service import ResearchService
from research.evaluation.results import CaseResult, EvaluationReport
from research.evaluation.fixtures import FixtureWeb, ReplayModel
from research.evaluation.schema import EvaluationCase, EvaluationSuite
from research.prompts import DIRECT_RESEARCH_INSTRUCTIONS
from research.tools.firecrawl import Firecrawl


class ResearchEvaluation:
    async def run(self, suite: EvaluationSuite, model: Model | None = None) -> EvaluationReport:
        results = [await self._run_case(case, model) for case in suite.cases]
        return EvaluationReport(
            suite_version=suite.version,
            suite_sha256=hashlib.sha256(suite.model_dump_json().encode()).hexdigest(),
            prompt_sha256=hashlib.sha256(DIRECT_RESEARCH_INSTRUCTIONS.encode()).hexdigest(),
            mode='model' if model else 'replay',
            model=model.model_name if model else 'controlled-replay',
            model_quality_assessed=False,
            manual_review_required=['query relevance', 'claim support and uncertainty', 'answer usefulness'],
            scope='Fixed web fixtures; no live search reliability assessment. Automatic checks are lexical, not semantic.',
            passed=all(result.passed for result in results),
            cases=results,
        )

    async def _run_case(self, case: EvaluationCase, model: Model | None) -> CaseResult:
        web = FixtureWeb(case)
        selected = model or FunctionModel(ReplayModel(case).respond)
        answer = ''
        error: str | None = None
        start = perf_counter()
        with capture_run_messages() as messages:
            try:
                async with httpx2.AsyncClient(
                    base_url='http://evaluation.invalid', transport=httpx2.MockTransport(web.respond),
                ) as client:
                    answer = await ResearchService.run_with_dependencies(case.question, {'main': selected}, Firecrawl(client))
            except Exception as exc:
                error = user_error_message(exc)
        result = self._assess(case, answer, messages)
        result.error = error
        result.elapsed_seconds = round(perf_counter() - start, 3)
        result.unmatched_urls = web.unmatched
        result.passed = result.passed and error is None and not web.unmatched
        return result

    def _assess(self, case: EvaluationCase, answer: str, messages: list[ModelMessage]) -> CaseResult:
        calls = [part for message in messages for part in message.parts if isinstance(part, ToolCallPart)]
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        reads = [dict(part.content) for part in returns
                 if part.tool_name == 'read_page' and isinstance(part.content, dict) and 'content' in part.content]
        searches = [dict(part.content) for part in returns if part.tool_name == 'search' and isinstance(part.content, dict)]
        evidence = '\n'.join(str(item.get('content', '')) for item in reads)
        read_urls = {urldefrag(str(item.get('url', '')))[0] for item in reads}
        cited_urls = re.findall(r'https?://[^\s<>\)\]]+', answer)
        expected = case.expect
        queries = {str(query) for call in calls if call.tool_name == 'search'
                   for query in call.args_as_dict().get('queries', [])}
        evidence_checks = {
            'required_content': all(text.casefold() in evidence.casefold() for text in expected.evidence_contains),
            'excluded_content': all(text.casefold() not in evidence.casefold() for text in expected.evidence_excludes),
            'search_attempts': len(searches) >= expected.min_searches,
            'distinct_search_queries': len(queries) >= expected.min_distinct_queries,
            'search_failure_visible': any(item.get('failed_queries', 0) for item in searches) == expected.search_failure,
            'read_budget': sum(part.tool_name == 'read_page' for part in calls) <= expected.max_read_calls,
            'content_budget': len(evidence) <= expected.max_evidence_characters,
        }
        answer_checks = {
            'required_content': all(text.casefold() in answer.casefold() for text in expected.answer_contains),
            'excluded_content': all(text.casefold() not in answer.casefold() for text in expected.answer_excludes),
            'required_citations': all(url in answer for url in expected.citations),
            'citations_read': all(urldefrag(url.rstrip('.,;'))[0] in read_urls for url in cited_urls),
            'nonempty': bool(answer.strip()),
        }
        responses = [message for message in messages if isinstance(message, ModelResponse)]
        return CaseResult(
            id=case.id, question=case.question, answer=answer,
            evidence_checks=evidence_checks, answer_checks=answer_checks,
            passed=all(evidence_checks.values()) and all(answer_checks.values()),
            model_calls=len(responses),
            input_tokens=sum(message.usage.input_tokens for message in responses),
            output_tokens=sum(message.usage.output_tokens for message in responses),
            evidence_characters=len(evidence),
            tool_calls=[{'tool': call.tool_name, 'arguments': call.args_as_dict()} for call in calls],
            evidence=reads,
            search_results=searches,
            warnings=['First page excerpt lacked the required evidence.'] if reads and any(
                text.casefold() not in str(reads[0].get('content', '')).casefold()
                for text in expected.evidence_contains
            ) else [],
        )

"""Restricted public research: approved queries, observed URLs and quoted evidence only."""

import json
from datetime import date
from dataclasses import asdict, replace
from urllib.parse import urlsplit, urlunsplit

import httpx2
from django.conf import settings
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from core.tracing import tracing
from core.turns import OwnerTurn
from grievances import constants
from grievances.policy_records import PolicySource, ResearchFinding
from grievances.policy_store import PolicyStore, ResearchBriefView
from grievances.prompts import POLICY_INSTRUCTIONS
from grievances.schema import PolicyExtraction, PublicResearchBrief
from grievances.store import CaseStore, CaseView, SelectionView
from research.tools.firecrawl import Firecrawl


class PublicResearchBoundary:
    """Application-owned allowlist; no model-selected query or URL reaches Firecrawl."""

    def __init__(self, owner_id: str, store: CaseStore) -> None:
        self.owner_id = owner_id
        self.store = store

    @staticmethod
    def canonical_url(value: str) -> str | None:
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or parsed.port not in (None, 443)
                    or '%' in value or not Firecrawl.is_public_url(value)):
                return None
            return urlunsplit(('https', parsed.netloc.lower(), parsed.path or '/', '', ''))
        except ValueError:
            return None

    async def allowed(self, text: str) -> bool:
        return not await self.store.contains_private_material(self.owner_id, text)

    async def validate_brief(self, brief: PublicResearchBrief) -> bool:
        fields = brief.model_dump()
        for value in fields.values():
            if not await self.allowed(value):
                return False
        for value in (brief.company, brief.jurisdiction):
            if not any(char.isalnum() for char in value) or any(not (char.isalnum() or char in " .&'’-(),") for char in value):
                return False
        url = self.canonical_url(brief.official_site)
        return bool(url and urlsplit(url).path == '/')

    async def query(self, brief: ResearchBriefView, purpose: str) -> str | None:
        # Called only with fixed application purposes, never a model argument.
        if purpose not in ('refund policy', 'complaint procedure', 'support contact'):
            return None
        query = f'{brief.company} {brief.product} {brief.issue} {brief.jurisdiction} {purpose}'
        return query if await self.allowed(query) else None


class PolicyResearchService:
    def __init__(self, model: Model, case_store: CaseStore, firecrawl: Firecrawl | None = None) -> None:
        self.model = model
        self.case_store = case_store
        self.store = PolicyStore()
        self.firecrawl = firecrawl

    async def run(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, usage: RunUsage) -> str:
        brief = await self.store.latest_brief(turn.owner_id, current.id)
        if brief is None or not brief.confirmed or brief.fact_revision != current.revision:
            return 'Please review and explicitly confirm the current public research fields and official site first. Factual drafting remains available.'
        with tracing.operation('case.policy_research', {}, kind='agent', private=True):
            if self.firecrawl is not None:
                return await self._research(turn, selection, current, brief, usage, self.firecrawl)
            headers = {'Authorization': f'Bearer {settings.FIRECRAWL_API_KEY}'} if settings.FIRECRAWL_API_KEY else {}
            async with httpx2.AsyncClient(base_url=settings.FIRECRAWL_BASE_URL, headers=headers, follow_redirects=False) as client:
                return await self._research(turn, selection, current, brief, usage, Firecrawl(client))

    async def _research(self, turn: OwnerTurn, selection: SelectionView, current: CaseView, brief: ResearchBriefView,
                        usage: RunUsage, firecrawl: Firecrawl) -> str:
        boundary = PublicResearchBoundary(turn.owner_id, self.case_store)
        if not await boundary.validate_brief(PublicResearchBrief.model_validate(brief.public_fields())):
            return 'The reviewed brief no longer passes the public-data boundary. Please review it again. No research was sent.'
        anchor = boundary.canonical_url(brief.official_site)
        if anchor is None:
            return 'The official site is not a valid public HTTPS root. Please review it again.'
        origin = urlsplit(anchor).netloc
        queries = [query for purpose in ('refund policy', 'complaint procedure', 'support contact')
                   if (query := await boundary.query(brief, purpose)) is not None]
        if len(queries) != 3:
            return 'The public research query could not be validated. No research was sent.'
        # Actual tool arguments are generated here, not by a language model.
        discovery = await firecrawl.search(queries)
        discovery_urls = {item['url'] for item in discovery.get('sources', []) if isinstance(item, dict) and isinstance(item.get('url'), str)}
        queue: list[tuple[str, str]] = [(anchor, '')]
        visited: set[str] = set()
        sources: list[PolicySource] = []
        failed = bool(discovery.get('error') or discovery.get('failed_queries'))
        while queue and len(visited) < constants.POLICY_MAX_PAGES:
            url, parent_url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            if boundary.canonical_url(url) != url or urlsplit(url).netloc != origin or not await boundary.allowed(url):
                failed = True
                continue
            page = await firecrawl.inspect_page(url)
            final_url = boundary.canonical_url(str(page.get('final_url', '')))
            content = page.get('content')
            status = page.get('status_code')
            if (page.get('error') or page.get('final_url_verified') is not True or not final_url
                    or urlsplit(final_url).netloc != origin or not isinstance(status, int) or not 200 <= status < 300
                    or not isinstance(content, str) or not content.strip() or page.get('truncated') is not False):
                failed = True
                continue
            if sum(len(source.content) for source in sources) + len(content) > constants.POLICY_CONTEXT_CHARS:
                failed = True
                continue
            source = PolicySource(f'source-{len(sources) + 1}', url, final_url,
                                  str(page.get('retrieved_at', '')), content, parent_url,
                                  'owner_confirmed', anchor)
            sources.append(source)
            links = [link for link in page.get('links', []) if isinstance(link, str)]
            links.sort(key=lambda link: link not in discovery_urls)
            for link in links:
                canonical = boundary.canonical_url(link)
                if canonical and urlsplit(canonical).netloc == origin and canonical not in visited and await boundary.allowed(canonical):
                    queue.append((canonical, final_url))
        uncertainty = 'Policy applicability and purchase-time terms are not established; no refund entitlement is asserted. Please provide relevant purchase-time terms or confirm applicability with the company.'
        if not sources or sources[0].requested_url != anchor:
            return await self.store.save(turn, selection, current, brief, 'unverified', [], [],
                'Official identity or final destination could not be established. ' + uncertainty)
        agent: Agent[None, PolicyExtraction] = Agent(self.model, output_type=PolicyExtraction,
            instructions=POLICY_INSTRUCTIONS, name='official_policy', retries=constants.VALIDATION_RETRIES,
            model_settings={'max_tokens': constants.MAX_OUTPUT_TOKENS})
        agent.instrument = tracing.private_instrumentation()
        public_payload = json.dumps({'public_brief': brief.public_fields(), 'sources': [asdict(source) for source in sources]})
        if len(public_payload) > constants.POLICY_CONTEXT_CHARS:
            return await self.store.save(turn, selection, current, brief, 'unverified', [], [],
                'Public evidence exceeded the bounded research context. ' + uncertainty)
        # No selected case, private dates, order data, preferences or transcripts enter this agent.
        extraction = (await agent.run(public_payload, usage=usage,
            usage_limits=UsageLimits(request_limit=constants.MAX_MODEL_REQUESTS), timeout=constants.RUN_TIMEOUT_SECONDS)).output
        by_id = {source.id: source for source in sources}
        identity = by_id.get(extraction.identity_source_id)
        if (identity is None or not extraction.identity_quote or extraction.identity_quote not in identity.content
                or brief.company.casefold() not in extraction.identity_quote.casefold()
                or len(extraction.identity_quote) < len(brief.company) + 10):
            return await self.store.save(turn, selection, current, brief, 'unverified', [], [],
                'The owner-confirmed site lacks validated company identity evidence. ' + uncertainty)
        sources = [replace(source, identity_url=identity.url, identity_quote=extraction.identity_quote) for source in sources]
        findings: list[ResearchFinding] = []
        for proposed in extraction.findings:
            found_source = by_id.get(proposed.source_id)
            if found_source is None or proposed.quote not in found_source.content:
                continue
            if any(value and value not in proposed.quote for value in (proposed.effective_from, proposed.effective_until)):
                continue
            applicability = 'uncertain'
            try:
                purchased = date.fromisoformat(current.facts.get('purchase_date', ''))
                start = date.fromisoformat(proposed.effective_from) if proposed.effective_from else None
                end = date.fromisoformat(proposed.effective_until) if proposed.effective_until else None
                if start and start > purchased:
                    applicability = 'published_after_purchase'
                elif start and end and start <= purchased <= end:
                    applicability = 'purchase_time_candidate_not_eligibility'
            except ValueError:
                pass
            findings.append(ResearchFinding(proposed.source_id, proposed.quote, proposed.kind, proposed.effective_from,
                proposed.effective_until, found_source.url, found_source.retrieved_at, applicability))
        if extraction.conflicting:
            uncertainty = 'Official policy statements conflict; no priority or entitlement is inferred. ' + uncertainty
        if failed:
            uncertainty = 'Some pages were unavailable or unverified. ' + uncertainty
        return await self.store.save(turn, selection, current, brief, 'verified_sources' if findings else 'unverified',
                                     sources, findings, uncertainty)

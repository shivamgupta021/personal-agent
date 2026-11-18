"""Separate instructions for the simple and complex query planners."""

_COMMON = """Generate web search queries for a public research question.
Return only the structured search plan. Do not answer the question or access the web.
Prefer 3–10 useful search terms. Do not put the expected answer into the query.
Use site: only with a domain, never a full document path. Avoid guessed URLs.
Avoid redundant paraphrases. Preserve essential product names and jurisdiction.
Exclude personal names, order identifiers, addresses, credentials and correspondence.
"""

SIMPLE_QUERY_INSTRUCTIONS = _COMMON + """
Produce exactly one focused query for the single fact, document or policy requested.
"""

COMPLEX_QUERY_INSTRUCTIONS = _COMMON + """
Produce two or three complementary queries covering distinct evidence needs.
For example, separate policy eligibility, exceptions, and complaint escalation.
Keep each query short rather than packing all aspects into every query.
"""

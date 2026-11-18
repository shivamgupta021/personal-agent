"""Versioned public research scenarios and explicit, limited automatic checks."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from research.evaluation.constants import MAX_EVIDENCE_CHARACTERS, MAX_READ_CALLS, MIN_DISTINCT_QUERIES, MIN_SEARCHES


class ReplayStep(BaseModel):
    tool: Literal['search', 'read_page'] | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    answer: str = ''


class Expectations(BaseModel):
    evidence_contains: list[str] = Field(default_factory=list)
    evidence_excludes: list[str] = Field(default_factory=list)
    answer_contains: list[str] = Field(default_factory=list)
    answer_excludes: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    min_searches: int = MIN_SEARCHES
    min_distinct_queries: int = MIN_DISTINCT_QUERIES
    max_read_calls: int = MAX_READ_CALLS
    max_evidence_characters: int = MAX_EVIDENCE_CHARACTERS
    search_failure: bool = False


class EvaluationCase(BaseModel):
    id: str
    question: str
    search_responses: list[dict[str, Any]] = Field(min_length=1)
    pages: dict[str, str] = Field(default_factory=dict)
    replay: list[ReplayStep] = Field(min_length=1)
    expect: Expectations


class EvaluationSuite(BaseModel):
    version: Literal[1]
    cases: list[EvaluationCase] = Field(min_length=1)

"""Typed evaluation records, serialized only at the command boundary."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaseResult:
    id: str
    question: str
    answer: str
    evidence_checks: dict[str, bool]
    answer_checks: dict[str, bool]
    passed: bool
    model_calls: int
    input_tokens: int
    output_tokens: int
    evidence_characters: int
    tool_calls: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    search_results: list[dict[str, Any]]
    warnings: list[str]
    elapsed_seconds: float = 0
    error: str | None = None
    unmatched_urls: list[str] = field(default_factory=list)


@dataclass
class EvaluationReport:
    suite_version: int
    suite_sha256: str
    prompt_sha256: str
    mode: str
    model: str
    model_quality_assessed: bool
    manual_review_required: list[str]
    scope: str
    passed: bool
    cases: list[CaseResult]
    experiment_url: str | None = None
    dataset_name: str | None = None

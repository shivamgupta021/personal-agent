"""Validated output of query planning, independent of web execution."""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from research import constants

PlannedQuery = Annotated[str, StringConstraints(
    strip_whitespace=True, min_length=1, max_length=constants.PLANNED_QUERY_CHARACTERS,
)]


class SearchPlan(BaseModel):
    queries: Annotated[list[PlannedQuery], Field(min_length=1, max_length=constants.SEARCH_MAX_QUERIES)]


class SimpleSearchPlan(SearchPlan):
    queries: Annotated[list[PlannedQuery], Field(min_length=1, max_length=1)]


class ComplexSearchPlan(SearchPlan):
    queries: Annotated[list[PlannedQuery], Field(min_length=2, max_length=constants.SEARCH_MAX_QUERIES)]

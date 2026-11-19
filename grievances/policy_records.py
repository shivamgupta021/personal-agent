"""Typed immutable evidence crossing research, persistence and drafting boundaries."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicySource:
    id: str
    requested_url: str
    url: str
    retrieved_at: str
    content: str
    linked_from: str
    trust_basis: str
    anchor_url: str
    identity_url: str = ''
    identity_quote: str = ''


@dataclass(frozen=True)
class ResearchFinding:
    source_id: str
    quote: str
    kind: str
    effective_from: str
    effective_until: str
    url: str
    retrieved_at: str
    applicability: str

"""Bounded proposals; quoted values stay distinguishable from model conclusions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grievances.constants import (MAX_FACT_CHARS, POLICY_MAX_FINDINGS, POLICY_MAX_QUOTE_CHARS,
                                  POLICY_PUBLIC_FIELD_CHARS, POLICY_SITE_URL_CHARS)

FactKey = Literal['company', 'purchase_context', 'issue', 'requested_remedy', 'purchase_date',
                  'delivery_date', 'amount_paid', 'refund_amount', 'currency', 'order_reference',
                  'evidence', 'customer_name', 'address', 'correspondence']


class SuppliedFact(BaseModel):
    key: FactKey
    value: str = Field(min_length=1, max_length=MAX_FACT_CHARS)
    quote: str = Field(min_length=1, max_length=MAX_FACT_CHARS)


class IntakeAction(BaseModel):
    action: Literal['create', 'list', 'select', 'show', 'update', 'leave', 'public', 'memory', 'clarify', 'draft', 'list_drafts', 'read_draft', 'review_research', 'confirm_research', 'research']
    case_id: str = ''
    public_brief: 'PublicResearchBrief | None' = None
    draft_version: int | None = Field(default=None, ge=1)
    label: str = Field(default='', max_length=120)
    intent_quote: str = Field(default='', max_length=500, description='Required for create/select/leave: exact current user words explicitly requesting this action.')
    correction_quote: str = Field(default='', max_length=500)
    facts: list[SuppliedFact] = Field(default_factory=list, max_length=16)


DraftFactKey = Literal['company', 'purchase_context', 'purchase_date', 'delivery_date',
                       'order_reference', 'issue', 'amount_paid', 'refund_amount', 'currency', 'evidence']


class DraftPlan(BaseModel):
    """A writing plan has references and style choices, never invented factual prose."""

    model_config = ConfigDict(extra='forbid')
    fact_order: list[DraftFactKey] = Field(default_factory=list, max_length=10)
    greeting: Literal['hello', 'dear_support'] = 'dear_support'
    needs_clarification: bool = False


class PublicResearchBrief(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company: str = Field(min_length=1, max_length=POLICY_PUBLIC_FIELD_CHARS)
    product: Literal['electrical appliance', 'clothing', 'travel', 'digital service', 'other goods', 'other service']
    issue: Literal['defective item', 'not delivered', 'billing issue', 'service issue']
    jurisdiction: str = Field(min_length=1, max_length=POLICY_PUBLIC_FIELD_CHARS)
    official_site: str = Field(min_length=1, max_length=POLICY_SITE_URL_CHARS)


class PolicyFinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str
    quote: str = Field(min_length=1, max_length=POLICY_MAX_QUOTE_CHARS)
    kind: Literal['policy', 'procedure', 'support']
    effective_from: str = ''
    effective_until: str = ''


class PolicyExtraction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    identity_source_id: str = ''
    identity_quote: str = Field(default='', max_length=POLICY_MAX_QUOTE_CHARS)
    findings: list[PolicyFinding] = Field(default_factory=list, max_length=POLICY_MAX_FINDINGS)
    conflicting: bool = False

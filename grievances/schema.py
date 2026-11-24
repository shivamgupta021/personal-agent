"""Bounded proposals; quoted values stay distinguishable from model conclusions."""

from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from conversations.constants import MAX_INLINE_REPLY_CHARS

from grievances.constants import (MAX_FOLLOWUP_REPLIES, MAX_REPLY_CHARS, MAX_FACT_CHARS, POLICY_MAX_FINDINGS, POLICY_MAX_QUOTE_CHARS,
                                  POLICY_PUBLIC_FIELD_CHARS, POLICY_SITE_URL_CHARS)

FactKey = Literal['company', 'purchase_context', 'issue', 'requested_remedy', 'purchase_date',
                  'delivery_date', 'amount_paid', 'refund_amount', 'currency', 'order_reference',
                  'evidence', 'customer_name', 'address', 'correspondence']


class SuppliedFact(BaseModel):
    key: FactKey
    value: str = Field(min_length=1, max_length=MAX_FACT_CHARS)
    quote: str = Field(min_length=1, max_length=MAX_FACT_CHARS)


class IntakeAction(BaseModel):
    action: Literal['preference', 'list_preferences', 'gmail_connect', 'gmail_reminder_set', 'gmail_reminder_status', 'gmail_reminder_cancel', 'gmail_reminder_clarify', 'gmail_monitor_start', 'gmail_monitor_stop', 'gmail_monitor_status', 'case_help', 'gmail_open_draft', 'gmail_check_replies', 'gmail_search', 'gmail_select', 'gmail_show', 'delete', 'create', 'list', 'select', 'show', 'update', 'leave', 'public', 'conversation', 'memory', 'clarify', 'draft', 'list_drafts', 'read_draft', 'review_research', 'confirm_research', 'research', 'record_sent', 'record_reply', 'resolve', 'close_without_resolution', 'follow_up']
    clarification_reason: Literal['unspecified', 'deleted_details'] = Field(default='unspecified',
        description='Use deleted_details only when the owner asks about retrieving details of a deleted case; this selects a fixed explanation, not a deletion action.')
    reminder_kind: Literal['no_response', 'draft_review'] | None = Field(default=None, description='Set for a new purpose; leave null when moving an existing reminder.')
    conversation_reply: str = Field(default='', max_length=MAX_INLINE_REPLY_CHARS, description='Optional final answer for ordinary conversation only; leave empty when recall, compaction or memory tools are needed.')
    reminder_at: datetime | None = None
    deadline_quote: str = Field(default='', max_length=500)
    reply_is_substantive: bool | None = None
    gmail_account: str = Field(default='', max_length=320)
    gmail_compose: bool = False
    gmail_draft: bool = False
    gmail_revision: bool = False
    gmail_recipient: str = Field(default='', max_length=320)
    gmail_reference: str = Field(default='', max_length=128)
    case_id: str = ''
    public_brief: 'PublicResearchBrief | None' = None
    draft_version: int | None = Field(default=None, ge=1)
    draft_follow_up: bool = False
    reply_text: str = Field(default='', max_length=MAX_REPLY_CHARS)
    sent_details: str = Field(default='', max_length=MAX_FACT_CHARS)
    label: str = Field(default='', max_length=120)
    intent_quote: str = Field(default='', max_length=500, description='Required for create/select/leave: exact current user words explicitly requesting this action.')
    correction_quote: str = Field(default='', max_length=500)
    facts: list[SuppliedFact] = Field(default_factory=list, max_length=16)


DraftFactKey = Literal['company', 'purchase_context', 'purchase_date', 'delivery_date',
                       'order_reference', 'issue', 'amount_paid', 'refund_amount', 'currency', 'evidence']


class ReplyExcerpt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reply_id: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=MAX_REPLY_CHARS)


class DraftPlan(BaseModel):
    """A writing plan has references and style choices, never invented factual prose."""

    model_config = ConfigDict(extra='forbid')
    fact_order: list[DraftFactKey] = Field(default_factory=list, max_length=10)
    greeting: Literal['hello', 'dear_support'] = 'dear_support'
    needs_clarification: bool = False
    reply_references: list[ReplyExcerpt] = Field(default_factory=list, max_length=MAX_FOLLOWUP_REPLIES)
    sent_report_ids: list[int] = Field(default_factory=list, max_length=MAX_FOLLOWUP_REPLIES)


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

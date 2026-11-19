"""Bounded proposals; quoted values stay distinguishable from model conclusions."""

from typing import Literal

from pydantic import BaseModel, Field

from grievances.constants import MAX_FACT_CHARS

FactKey = Literal['company', 'purchase_context', 'issue', 'requested_remedy', 'purchase_date',
                  'delivery_date', 'amount_paid', 'refund_amount', 'currency', 'order_reference',
                  'evidence', 'customer_name', 'address', 'correspondence']


class SuppliedFact(BaseModel):
    key: FactKey
    value: str = Field(min_length=1, max_length=MAX_FACT_CHARS)
    quote: str = Field(min_length=1, max_length=MAX_FACT_CHARS)


class IntakeAction(BaseModel):
    action: Literal['create', 'list', 'select', 'show', 'update', 'leave', 'public', 'memory', 'clarify']
    case_id: str = ''
    label: str = Field(default='', max_length=120)
    intent_quote: str = Field(default='', max_length=500, description='Required for create/select/leave: exact current user words explicitly requesting this action.')
    correction_quote: str = Field(default='', max_length=500)
    facts: list[SuppliedFact] = Field(default_factory=list, max_length=16)

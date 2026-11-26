"""Validated preference intent shared by routing and execution."""
from typing import Literal
from pydantic import BaseModel, Field
from preferences import constants


class PreferenceIntent(BaseModel):
    action: Literal['save', 'list', 'forget', 'clarify', 'none']
    key: str = Field(default='', max_length=constants.KEY_CHARS, pattern=r'^[a-z0-9_]*$')
    value: str = Field(default='', max_length=constants.VALUE_CHARS)
    consent_quote: str = Field(default='', max_length=constants.SOURCE_CHARS)
    value_quote: str = Field(default='', max_length=constants.VALUE_CHARS)
    suggestion_quote: str = Field(default='', max_length=constants.SOURCE_CHARS)
    question: str = Field(default='', max_length=constants.VALUE_CHARS)


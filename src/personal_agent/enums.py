"""Shared execution vocabulary."""

from enum import StrEnum


class RunStatus(StrEnum):
    OK = 'ok'
    TIMEOUT = 'timeout'
    LIMIT = 'limit'
    CANCELLED = 'cancelled'
    ERROR = 'error'


class ToolName(StrEnum):
    SEARCH = 'search'
    READ_PAGE = 'read_page'

"""Translate failures at the application boundary without exposing provider payloads."""

import asyncio

from pydantic_ai.exceptions import RunCancelled, UsageLimitExceeded

from personal_agent.enums import RunStatus

SAFE_CONFIGURATION_ERRORS = {
    'Run the login command first.',
    'Another command is using this subscription session.',
    'Could not open the browser. Run login from a desktop terminal.',
    'Sign-in completed, but plan usage was not granted. Run login again.',
    'Set both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.',
}


def failure_status(error: BaseException) -> RunStatus:
    if isinstance(error, TimeoutError):
        return RunStatus.TIMEOUT
    if isinstance(error, UsageLimitExceeded):
        return RunStatus.LIMIT
    if isinstance(error, (asyncio.CancelledError, RunCancelled, KeyboardInterrupt)):
        return RunStatus.CANCELLED
    return RunStatus.ERROR


def user_error_message(error: Exception) -> str:
    if type(error) is RuntimeError and str(error) in SAFE_CONFIGURATION_ERRORS:
        return str(error)
    match failure_status(error):
        case RunStatus.TIMEOUT:
            return 'Research timed out. Try a narrower question.'
        case RunStatus.LIMIT:
            return 'Research stopped at its execution limit. Try a narrower question.'
        case RunStatus.CANCELLED:
            return 'Research cancelled.'
        case _:
            return 'Research failed. Check service connectivity and subscription access. Provider details are hidden.'

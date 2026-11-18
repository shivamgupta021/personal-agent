"""Translate failures at the application boundary without exposing provider payloads."""

from pydantic_ai.exceptions import RunCancelled, UsageLimitExceeded

SAFE_CONFIGURATION_ERRORS = {
    'Run the login command first.',
    'Another command is using this subscription session.',
    'Could not open the browser. Run login from a desktop terminal.',
    'Sign-in completed, but plan usage was not granted. Run login again.',
}


def user_error_message(error: Exception) -> str:
    if type(error) is RuntimeError and str(error) in SAFE_CONFIGURATION_ERRORS:
        return str(error)
    if isinstance(error, TimeoutError):
        return 'Research timed out. Try a narrower question.'
    if isinstance(error, UsageLimitExceeded):
        return 'Research stopped at its execution limit. Try a narrower question.'
    if isinstance(error, RunCancelled):
        return 'Research cancelled.'
    return 'Command failed. Check service connectivity and subscription access. Provider details are hidden.'

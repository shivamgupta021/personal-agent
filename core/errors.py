"""Translate failures at the application boundary without exposing provider payloads."""

from pydantic_ai.exceptions import RunCancelled, UsageLimitExceeded

class ReplySavedDraftFailed(Exception):
    """The correspondence commit succeeded but its requested generation did not."""


SAFE_CONFIGURATION_ERRORS = {
    'Run the login command first.',
    'Another command is using this subscription session.',
    'Could not open the browser. Run login from a desktop terminal.',
    'Sign-in completed, but plan usage was not granted. Run login again.',
}


def user_error_message(error: Exception) -> str:
    if isinstance(error, ReplySavedDraftFailed):
        return 'Company reply saved; the follow-up draft could not finish. Request a follow-up again. No message was sent to the company.'
    if type(error) is RuntimeError and str(error) in SAFE_CONFIGURATION_ERRORS:
        return str(error)
    if isinstance(error, TimeoutError):
        return 'This request timed out. Please try again.'
    if isinstance(error, UsageLimitExceeded):
        return 'This request reached its execution limit. Please try a smaller task.'
    if isinstance(error, RunCancelled):
        return 'This request was cancelled.'
    return 'Command failed. Check service connectivity and subscription access. Provider details are hidden.'

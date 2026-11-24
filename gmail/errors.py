class GmailError(Exception):
    """A sanitized message suitable for display to the local owner."""


class DraftOutcomeUnknown(GmailError):
    """Creation may have reached Google; never repeat it automatically."""


class GmailDisconnected(GmailError):
    """No active credential slot; offer owner-driven onboarding."""

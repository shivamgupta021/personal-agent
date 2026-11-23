class GmailError(Exception):
    """A sanitized message suitable for display to the local owner."""


class DraftOutcomeUnknown(GmailError):
    """Creation may have reached Google; never repeat it automatically."""

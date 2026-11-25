class GmailError(Exception):
    """A sanitized message suitable for display to the local owner."""


class DraftOutcomeUnknown(GmailError):
    """Creation may have reached Google; never repeat it automatically."""


class GmailDisconnected(GmailError):
    """No active credential slot; offer owner-driven onboarding."""


class GmailBusy(GmailError):
    """Another operation owns the credential lease; background work should defer."""


class GmailComposeRequired(GmailError):
    """Read access is valid, but the requested draft needs additional consent."""

"""Application settings for controlled tests; never export to live Langfuse."""

from config.settings import *  # noqa: F403


LANGFUSE_PUBLIC_KEY = ''
LANGFUSE_SECRET_KEY = ''

# Debugging flags from the developer .env must not change test privacy expectations.
LANGFUSE_CAPTURE_PRIVATE_CONTENT = False

LANGFUSE_CAPTURE_EMAIL_CONTENT = False

# Test both routing modes explicitly; local rollout settings must not select fixtures.
ROUTINE_TOOLS_ENABLED = False

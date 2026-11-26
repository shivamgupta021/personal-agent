"""Bounded, case-approved automatic correspondence policy."""
from django.db import models

DEFAULT_INTERVAL_MINUTES = 2880
DEFAULT_MAX_FOLLOWUPS = 3
MAX_FOLLOWUPS = 5
MAX_INTERVAL_MINUTES = 43200
MAX_BODY_WORDS = 150
MAX_BODY_CHARS = 2000
MAX_DECISION_TOKENS = 1200
MAX_REASON_CHARS = 500
MAX_JOBS_PER_TICK = 2
LEASE_SECONDS = 300


class FollowupStatus(models.TextChoices):
    PROPOSED = 'proposed'
    ACTIVE = 'active'
    PAUSED = 'paused'
    STOPPED = 'stopped'
    COMPLETE = 'complete'
    UNKNOWN = 'unknown'


class SendStatus(models.TextChoices):
    ATTEMPTED = 'attempted'
    SENT = 'sent'
    UNKNOWN = 'unknown'

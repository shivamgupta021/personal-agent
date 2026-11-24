"""Reminder states and date interpretation policy."""
from django.db import models

REMINDER_TIMEZONE = 'Asia/Kolkata'


class ReminderStatus(models.TextChoices):
    PENDING = 'pending', 'Waiting for deadline'
    QUEUED = 'queued', 'Notification queued'
    NOTIFIED = 'notified', 'Notification handed off'
    CLARIFY = 'clarify', 'Reply needs owner clarification'
    CANCELLED = 'cancelled', 'Cancelled'
    SATISFIED = 'satisfied', 'Substantive reply received'


class ReminderKind(models.TextChoices):
    NO_RESPONSE = 'no_response', 'No new response'
    DRAFT_REVIEW = 'draft_review', 'Review an unsent draft'

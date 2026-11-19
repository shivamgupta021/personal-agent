"""Explicit preferences have independent ownership from conversation history."""

from __future__ import annotations

from datetime import datetime
from django.db import models


class SavedPreference(models.Model):
    identity: models.CharField[str, str] = models.CharField(max_length=128)
    key: models.CharField[str, str] = models.CharField(max_length=80)
    value: models.CharField[str, str] = models.CharField(max_length=500)
    source_suggestion_id: models.CharField[str, str] = models.CharField(max_length=32, blank=True)
    source_message_id: models.CharField[str, str] = models.CharField(max_length=32)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['identity', 'key'], name='unique_owner_preference')]

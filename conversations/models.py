"""Durable completed context and content-free trace cleanup records."""

from __future__ import annotations

from django.db import models


class ConversationRecord(models.Model):
    identity: models.CharField[str, str] = models.CharField(max_length=128, unique=True)
    history = models.JSONField(default=list)
    archive = models.JSONField(default=dict)


class ConversationTrace(models.Model):
    identity: models.CharField[str, str] = models.CharField(max_length=128, db_index=True)
    trace_id: models.CharField[str, str] = models.CharField(max_length=32, unique=True)
    pending_deletion: models.BooleanField[bool, bool] = models.BooleanField(default=False)


class ConversationSummary(models.Model):
    conversation: models.ForeignKey[ConversationRecord, ConversationRecord] = models.ForeignKey(ConversationRecord, on_delete=models.CASCADE)
    snapshot = models.JSONField()

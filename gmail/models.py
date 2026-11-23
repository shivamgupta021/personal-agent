"""Consent and case-owned correspondence; Gmail originals are never modified."""
from __future__ import annotations

from datetime import datetime

from django.db import models

from grievances.models import GrievanceCase, ComplaintDraft


class EmailConsent(models.Model):
    owner_id: models.CharField[str, str] = models.CharField(max_length=128, unique=True)
    accepted_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)


class CaseMailbox(models.Model):
    case: models.OneToOneField[GrievanceCase, GrievanceCase] = models.OneToOneField(GrievanceCase, on_delete=models.CASCADE)
    mailbox: models.EmailField[str, str] = models.EmailField()
    candidates = models.JSONField(default=list)
    selected_thread: models.CharField[str, str] = models.CharField(max_length=128, default='')
    messages = models.JSONField(default=list)
    truncated: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    pending_goal: models.TextField[str, str] = models.TextField(default='')
    pending_recipient: models.CharField[str, str] = models.CharField(max_length=320, default='')
    pending_question: models.TextField[str, str] = models.TextField(default='')
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now=True)


class GmailDraftExport(models.Model):
    draft: models.OneToOneField[ComplaintDraft, ComplaintDraft] = models.OneToOneField('grievances.ComplaintDraft', on_delete=models.CASCADE)
    mailbox: models.EmailField[str, str] = models.EmailField()
    recipient: models.EmailField[str, str] = models.EmailField()
    thread_id: models.CharField[str, str] = models.CharField(max_length=128, default='')
    fingerprint: models.CharField[str, str] = models.CharField(max_length=64)
    status: models.CharField[str, str] = models.CharField(max_length=16, default='pending')
    gmail_id: models.CharField[str, str] = models.CharField(max_length=128, default='')
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)

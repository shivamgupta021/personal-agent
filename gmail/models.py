"""Consent and case-owned correspondence; Gmail originals are never modified."""
from __future__ import annotations

from datetime import datetime

from django.db import models

from grievances.models import GrievanceCase, ComplaintDraft
from gmail.reminder_constants import ReminderKind


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
    message_id: models.CharField[str, str] = models.CharField(max_length=128, default='')
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)


class GmailReplyCheckState(models.Model):
    analysis_fingerprint: models.CharField[str, str] = models.CharField(max_length=64, default='')
    mailbox_record: models.OneToOneField[CaseMailbox, CaseMailbox] = models.OneToOneField(CaseMailbox, on_delete=models.CASCADE)
    seen_message_ids = models.JSONField(default=list)
    findings = models.JSONField(default=list)
    sent_matches = models.JSONField(default=list)
    last_summary: models.TextField[str, str] = models.TextField(default='')
    mailbox: models.EmailField[str, str] = models.EmailField(default='')
    thread_id: models.CharField[str, str] = models.CharField(max_length=128, default='')
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now=True)


class CaseMonitor(models.Model):
    mailbox_record: models.OneToOneField[CaseMailbox, CaseMailbox] = models.OneToOneField(CaseMailbox, on_delete=models.CASCADE)
    active: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    generation: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    mailbox: models.EmailField[str, str] = models.EmailField()
    thread_id: models.CharField[str, str] = models.CharField(max_length=128)
    seen_message_ids = models.JSONField(default=list)
    next_check: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    failures: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    failure_alerted: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    stopped_reason: models.CharField[str, str] = models.CharField(max_length=64, default='')


class MonitorNotification(models.Model):
    origin_trace_id: models.CharField[str, str] = models.CharField(max_length=32, default='')
    monitor: models.ForeignKey[CaseMonitor, CaseMonitor] = models.ForeignKey(CaseMonitor, on_delete=models.CASCADE)
    generation: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    text: models.TextField[str, str] = models.TextField()
    reminder_revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    cancelled: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    attempted: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    delivered: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)


class CaseReminder(models.Model):
    kind: models.CharField[str, str] = models.CharField(max_length=24, choices=ReminderKind.choices, default=ReminderKind.NO_RESPONSE)
    draft_version: models.PositiveIntegerField[int | None, int | None] = models.PositiveIntegerField(null=True)
    monitor: models.OneToOneField[CaseMonitor, CaseMonitor] = models.OneToOneField(CaseMonitor, on_delete=models.CASCADE)
    due_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    status: models.CharField[str, str] = models.CharField(max_length=16, default='pending')
    sent_provenance: models.CharField[str, str] = models.CharField(max_length=32)
    sent_reference: models.CharField[str, str] = models.CharField(max_length=128)
    baseline_message_ids = models.JSONField(default=list)
    dismissed_message_ids = models.JSONField(default=list)
    question_message_ids = models.JSONField(default=list)
    reason: models.TextField[str, str] = models.TextField(default='')


class CaseFollowup(models.Model):
    """One explicit approval binds the first message and subsequent send limits."""
    case: models.OneToOneField[GrievanceCase, GrievanceCase] = models.OneToOneField(GrievanceCase, on_delete=models.CASCADE)
    monitor: models.ForeignKey[CaseMonitor, CaseMonitor] = models.ForeignKey(CaseMonitor, on_delete=models.CASCADE)
    status: models.CharField[str, str] = models.CharField(max_length=16, default='proposed')
    generation: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=1)
    fact_revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    envelope = models.JSONField()
    baseline_ids = models.JSONField(default=list)
    draft_version: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    interval_minutes: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    max_followups: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    sent_count: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    next_due: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    approved_at: models.DateTimeField[datetime | None, datetime | None] = models.DateTimeField(null=True)
    reason: models.TextField[str, str] = models.TextField(default='')


class FollowupSend(models.Model):
    """Durable external handoff: an ambiguous attempt must never be retried blindly."""
    policy: models.ForeignKey[CaseFollowup, CaseFollowup] = models.ForeignKey(CaseFollowup, on_delete=models.CASCADE)
    generation: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    sequence: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    envelope = models.JSONField()
    status: models.CharField[str, str] = models.CharField(max_length=16, default='attempted')
    message_id: models.CharField[str, str] = models.CharField(max_length=128, default='')
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['policy', 'generation', 'sequence'], name='unique_followup_handoff')]


class FollowupApproval(models.Model):
    """Immutable terms of each owner approval, retained when the next plan replaces it."""
    policy: models.ForeignKey[CaseFollowup, CaseFollowup] = models.ForeignKey(CaseFollowup, on_delete=models.CASCADE)
    generation: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    approved_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    conversation_id: models.CharField[str, str] = models.CharField(max_length=128)
    delivery_id: models.CharField[str, str] = models.CharField(max_length=128)
    terms = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['policy', 'generation'], name='unique_followup_approval')]

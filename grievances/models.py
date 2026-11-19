"""Case-owned content and durable content-free delivery receipts."""

from __future__ import annotations

from uuid import UUID, uuid4
from datetime import datetime

from django.db import models


class GrievanceCase(models.Model):
    id: models.UUIDField[UUID, UUID] = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    owner_id: models.CharField[str, str] = models.CharField(max_length=128, db_index=True)
    label: models.CharField[str, str] = models.CharField(max_length=120)
    label_key: models.CharField[str, str] = models.CharField(max_length=120)
    status: models.CharField[str, str] = models.CharField(max_length=32, default='open')
    revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    progress_revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner_id', 'label_key'], name='unique_owner_case_label')]


class CaseSelection(models.Model):
    owner_id: models.CharField[str, str] = models.CharField(max_length=128)
    conversation_id: models.CharField[str, str] = models.CharField(max_length=128)
    case_id: UUID | None
    case: models.ForeignKey[GrievanceCase | None, GrievanceCase | None] = models.ForeignKey(GrievanceCase, null=True, on_delete=models.SET_NULL)
    revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner_id', 'conversation_id'], name='unique_case_selection')]


class CaseFactRevision(models.Model):
    case: models.ForeignKey[GrievanceCase, GrievanceCase] = models.ForeignKey(GrievanceCase, on_delete=models.CASCADE)
    revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    facts = models.JSONField(default=dict)
    sources = models.JSONField(default=dict)
    kind: models.CharField[str, str] = models.CharField(max_length=16)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['case', 'revision'], name='unique_case_fact_revision')]


class CaseMessage(models.Model):
    case: models.ForeignKey[GrievanceCase, GrievanceCase] = models.ForeignKey(GrievanceCase, on_delete=models.CASCADE)
    role: models.CharField[str, str] = models.CharField(max_length=16)
    text: models.TextField[str, str] = models.TextField()
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)


class CaseOperationReceipt(models.Model):
    owner_id: models.CharField[str, str] = models.CharField(max_length=128)
    conversation_id: models.CharField[str, str] = models.CharField(max_length=128)
    delivery_id: models.CharField[str, str] = models.CharField(max_length=128)
    action: models.CharField[str, str] = models.CharField(max_length=16)
    # Deliberately not a cascading FK: deleting content must not permit replay.
    case_id: models.UUIDField[UUID | None, UUID | None] = models.UUIDField(null=True)
    revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    outcome: models.CharField[str, str] = models.CharField(max_length=24)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner_id', 'conversation_id', 'delivery_id'], name='unique_case_delivery')]


class ComplaintDraft(models.Model):
    """Immutable text and source snapshot; usability is computed from current facts."""

    case: models.ForeignKey[GrievanceCase, GrievanceCase] = models.ForeignKey(GrievanceCase, on_delete=models.CASCADE)
    version: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    fact_revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    operation: models.OneToOneField[CaseOperationReceipt, CaseOperationReceipt] = models.OneToOneField(CaseOperationReceipt, on_delete=models.CASCADE)
    evidence_revision: models.PositiveIntegerField[int | None, int | None] = models.PositiveIntegerField(null=True)
    subject: models.TextField[str, str] = models.TextField()
    body: models.TextField[str, str] = models.TextField()
    missing_fields = models.JSONField(default=list)
    kind: models.CharField[str, str] = models.CharField(max_length=16, default='initial')
    progress_revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)
    reply_ids = models.JSONField(default=list)
    sent_report_ids = models.JSONField(default=list)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['case', 'version'], name='unique_case_draft_version')]


class CaseResearchBrief(models.Model):
    case: models.ForeignKey[GrievanceCase, GrievanceCase] = models.ForeignKey(GrievanceCase, on_delete=models.CASCADE)
    revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    fact_revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    fields = models.JSONField()
    confirmed: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)
    confirmed_at: models.DateTimeField[datetime | None, datetime | None] = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['case', 'revision'], name='unique_case_brief_revision')]


class CaseResearch(models.Model):
    case: models.ForeignKey[GrievanceCase, GrievanceCase] = models.ForeignKey(GrievanceCase, on_delete=models.CASCADE)
    revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    fact_revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    brief_revision: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    status: models.CharField[str, str] = models.CharField(max_length=32)
    sources = models.JSONField(default=list)
    findings = models.JSONField(default=list)
    uncertainty: models.TextField[str, str] = models.TextField()
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['case', 'revision'], name='unique_case_research_revision')]


class SentReport(models.Model):
    case: models.ForeignKey[GrievanceCase, GrievanceCase] = models.ForeignKey(GrievanceCase, on_delete=models.CASCADE)
    draft: models.ForeignKey[ComplaintDraft, ComplaintDraft] = models.ForeignKey(ComplaintDraft, on_delete=models.CASCADE)
    operation: models.OneToOneField[CaseOperationReceipt, CaseOperationReceipt] = models.OneToOneField(CaseOperationReceipt, on_delete=models.CASCADE)
    details: models.TextField[str, str] = models.TextField(default='')
    recorded_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)


class CompanyReply(models.Model):
    case: models.ForeignKey[GrievanceCase, GrievanceCase] = models.ForeignKey(GrievanceCase, on_delete=models.CASCADE)
    operation: models.OneToOneField[CaseOperationReceipt, CaseOperationReceipt] = models.OneToOneField(CaseOperationReceipt, on_delete=models.CASCADE)
    text: models.TextField[str, str] = models.TextField()
    recorded_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)


class CaseOutcome(models.Model):
    case: models.ForeignKey[GrievanceCase, GrievanceCase] = models.ForeignKey(GrievanceCase, on_delete=models.CASCADE)
    operation: models.OneToOneField[CaseOperationReceipt, CaseOperationReceipt] = models.OneToOneField(CaseOperationReceipt, on_delete=models.CASCADE)
    outcome: models.CharField[str, str] = models.CharField(max_length=32)
    owner_quote: models.TextField[str, str] = models.TextField()
    recorded_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)


class CaseOwnerState(models.Model):
    """Content-free serialization and invalidation boundary for one owner."""
    owner_id: models.CharField[str, str] = models.CharField(max_length=128, primary_key=True)
    deletion_epoch: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(default=0)


class CaseTrace(models.Model):
    """Single-case synthetic capture ownership; survives content deletion."""
    owner_id: models.CharField[str, str] = models.CharField(max_length=128)
    case_id: models.UUIDField[UUID, UUID] = models.UUIDField()
    trace_id: models.CharField[str, str] = models.CharField(max_length=32, unique=True)
    producer_closed: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    pending: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    observed: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    confirmed: models.BooleanField[bool, bool] = models.BooleanField(default=False)


class CaseTurnRegistration(models.Model):
    """Remember when a delivery began without retaining its request or response."""
    owner_id: models.CharField[str, str] = models.CharField(max_length=128)
    conversation_id: models.CharField[str, str] = models.CharField(max_length=128)
    delivery_id: models.CharField[str, str] = models.CharField(max_length=128)
    deletion_epoch: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner_id', 'conversation_id', 'delivery_id'], name='unique_registered_case_turn')]

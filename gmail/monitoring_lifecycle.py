"""Cancel case monitoring and dependent delivery state as one transition."""
from django.db import transaction
from django.db.models import F, QuerySet

from gmail.models import CaseMonitor, CaseReminder, MonitorNotification
from gmail.reminder_constants import ReminderStatus


class MonitoringLifecycle:
    @staticmethod
    def stop(monitors: QuerySet[CaseMonitor], reason: str) -> None:
        with transaction.atomic():
            identifiers = list(monitors.select_for_update().values_list('pk', flat=True))
            CaseReminder.objects.filter(monitor_id__in=identifiers,
                status__in=[ReminderStatus.PENDING, ReminderStatus.QUEUED, ReminderStatus.CLARIFY]).update(
                    status=ReminderStatus.CANCELLED, revision=F('revision') + 1, reason=reason)
            MonitorNotification.objects.filter(monitor_id__in=identifiers, attempted=False).update(cancelled=True)
            CaseMonitor.objects.filter(pk__in=identifiers).update(
                active=False, generation=F('generation') + 1, stopped_reason=reason)

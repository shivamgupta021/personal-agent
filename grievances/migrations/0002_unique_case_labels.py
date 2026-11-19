"""Preserve any existing intake records while making manual creation retries safe."""

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def populate_labels(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    case_model = apps.get_model('grievances', 'GrievanceCase')
    seen: set[tuple[str, str]] = set()
    for case in case_model.objects.using(schema_editor.connection.alias).order_by('created_at', 'id'):
        key = ' '.join(case.label.casefold().split())
        if (case.owner_id, key) in seen:
            case.label = f'{case.label[:80]} {case.pk}'
            key = ' '.join(case.label.casefold().split())
        seen.add((case.owner_id, key))
        case.label_key = key
        case.save(update_fields=['label', 'label_key'])


class Migration(migrations.Migration):
    dependencies = [('grievances', '0001_initial')]
    operations = [
        migrations.AddField(model_name='grievancecase', name='label_key', field=models.CharField(max_length=120, default=''), preserve_default=False),
        migrations.RunPython(populate_labels, migrations.RunPython.noop),
        migrations.AddConstraint(model_name='grievancecase', constraint=models.UniqueConstraint(fields=['owner_id', 'label_key'], name='unique_owner_case_label')),
    ]

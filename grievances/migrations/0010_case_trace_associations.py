"""Allow a trace to contain directory entries from multiple cases."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('grievances', '0009_caseturnregistration')]

    operations = [
        migrations.AlterField(model_name='casetrace', name='trace_id',
            field=models.CharField(max_length=32, db_index=True)),
        migrations.AddConstraint(model_name='casetrace',
            constraint=models.UniqueConstraint(fields=['owner_id', 'case_id', 'trace_id'],
                name='unique_case_trace_association')),
    ]

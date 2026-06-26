from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_snmp_sync", "0014_devicesnmpconfig_rename_device_to_sysname"),
    ]

    operations = [
        migrations.AddField(
            model_name="snmpsyncconfig",
            name="sync_stale_job_marker_minutes",
            field=models.PositiveIntegerField(
                default=120,
                help_text=(
                    "After this many minutes, a queued/running SNMP sync marker is considered stale and may be "
                    "cleared even if NetBox still shows the old job as active. Use 0 to disable automatic stale "
                    "marker cleanup for active-looking jobs."
                ),
            ),
        ),
    ]

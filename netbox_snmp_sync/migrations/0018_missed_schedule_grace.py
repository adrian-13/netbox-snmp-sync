from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_snmp_sync", "0017_vlan_subinterface_inference"),
    ]

    operations = [
        migrations.AddField(
            model_name="snmpsyncconfig",
            name="sync_missed_schedule_grace_minutes",
            field=models.PositiveIntegerField(
                default=360,
                help_text=(
                    "If a successful device's next scheduled sync is overdue by more than this many minutes, "
                    "treat it as missed scheduler downtime and re-anchor it instead of queueing a catch-up sync. "
                    "Use 0 to always catch up overdue schedules."
                ),
            ),
        ),
    ]

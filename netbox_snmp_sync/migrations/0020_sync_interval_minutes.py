from django.db import migrations, models


def hours_to_minutes(apps, schema_editor):
    """Preserve existing schedules' real-world timing: a config previously set to sync
    every N hours must keep running every N hours, now expressed as N*60 minutes."""
    DeviceSNMPConfig = apps.get_model("netbox_snmp_sync", "DeviceSNMPConfig")
    SNMPSyncConfig = apps.get_model("netbox_snmp_sync", "SNMPSyncConfig")
    DeviceSNMPConfig.objects.filter(sync_interval_minutes__isnull=False).update(
        sync_interval_minutes=models.F("sync_interval_minutes") * 60
    )
    SNMPSyncConfig.objects.all().update(
        sync_interval_minutes=models.F("sync_interval_minutes") * 60
    )


def minutes_to_hours(apps, schema_editor):
    DeviceSNMPConfig = apps.get_model("netbox_snmp_sync", "DeviceSNMPConfig")
    SNMPSyncConfig = apps.get_model("netbox_snmp_sync", "SNMPSyncConfig")
    DeviceSNMPConfig.objects.filter(sync_interval_minutes__isnull=False).update(
        sync_interval_minutes=models.F("sync_interval_minutes") / 60
    )
    SNMPSyncConfig.objects.all().update(
        sync_interval_minutes=models.F("sync_interval_minutes") / 60
    )


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_snmp_sync", "0019_devicesnmpconfig_vlan_group"),
    ]

    operations = [
        migrations.RenameField(
            model_name="devicesnmpconfig",
            old_name="sync_interval_hours",
            new_name="sync_interval_minutes",
        ),
        migrations.RenameField(
            model_name="snmpsyncconfig",
            old_name="sync_interval_hours",
            new_name="sync_interval_minutes",
        ),
        migrations.RunPython(hours_to_minutes, minutes_to_hours),
        migrations.AlterField(
            model_name="devicesnmpconfig",
            name="sync_interval_minutes",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Per-device minutes between automatic syncs. Blank = use global "
                          "setting; 0 disables interval sync for this device unless Sync at "
                          "hours is set.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="snmpsyncconfig",
            name="sync_interval_minutes",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Minutes between automatic syncs; 0 disables the interval scheduler "
                          "(unless specific hours are set below).",
            ),
        ),
    ]

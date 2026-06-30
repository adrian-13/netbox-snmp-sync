from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_snmp_sync", "0015_snmpsyncconfig_stale_job_marker_minutes"),
    ]

    operations = [
        migrations.AddField(
            model_name="devicesnmpconfig",
            name="sync_interfaces",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Override interface create/update sync for this device. Global = use global setting.",
                null=True,
                verbose_name="Sync interfaces",
            ),
        ),
        migrations.AddField(
            model_name="devicesnmpconfig",
            name="sync_ip_addresses",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Override IP address sync for this device. Global = use global setting.",
                null=True,
                verbose_name="Sync IP addresses",
            ),
        ),
        migrations.AddField(
            model_name="devicesnmpconfig",
            name="update_existing",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Override updating existing interfaces for this device. Global = use global setting.",
                null=True,
                verbose_name="Update existing objects",
            ),
        ),
        migrations.AddField(
            model_name="devicesnmpconfig",
            name="set_mac_address",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Override primary MAC address writes for this device. Global = use global setting.",
                null=True,
                verbose_name="Set MAC address",
            ),
        ),
        migrations.AddField(
            model_name="devicesnmpconfig",
            name="write_vlans",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Override per-interface VLAN membership writes for this device. Global = use global setting.",
                null=True,
                verbose_name="Write VLANs",
            ),
        ),
        migrations.AddField(
            model_name="devicesnmpconfig",
            name="create_vlans",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Override automatic VLAN creation for this device. Global = use global setting.",
                null=True,
                verbose_name="Create VLANs",
            ),
        ),
        migrations.AddField(
            model_name="snmpsyncconfig",
            name="sync_interfaces",
            field=models.BooleanField(default=True, help_text="Create and update interfaces from SNMP."),
        ),
        migrations.AddField(
            model_name="snmpsyncconfig",
            name="sync_ip_addresses",
            field=models.BooleanField(default=True, help_text="Create IP addresses from SNMP."),
        ),
    ]

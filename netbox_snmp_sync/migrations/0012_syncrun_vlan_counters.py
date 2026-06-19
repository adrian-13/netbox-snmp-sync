from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_snmp_sync", "0011_snmpsyncconfig_sync_job_timeout_seconds"),
    ]

    operations = [
        migrations.AddField(
            model_name="syncrun",
            name="vlans_created",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="syncrun",
            name="iface_vlans_set",
            field=models.PositiveIntegerField(default=0),
        ),
    ]

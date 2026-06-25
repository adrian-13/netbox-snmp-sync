from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_snmp_sync", "0013_syncrunchange"),
    ]

    operations = [
        migrations.AddField(
            model_name="devicesnmpconfig",
            name="rename_device_to_sysname",
            field=models.BooleanField(
                default=False,
                help_text="When applying SNMP sync, rename the NetBox device to the collected SNMP sysName.",
                verbose_name="Rename device to sysName",
            ),
        ),
    ]

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_snmp_sync", "0018_missed_schedule_grace"),
        ("ipam", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="devicesnmpconfig",
            name="vlan_group",
            field=models.ForeignKey(
                blank=True,
                help_text="Assign VLANs auto-created for this device to this group. Blank = no group.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="ipam.vlangroup",
                verbose_name="VLAN group",
            ),
        ),
    ]

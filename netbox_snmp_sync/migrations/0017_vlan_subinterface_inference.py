from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("netbox_snmp_sync", "0016_per_device_sync_behaviour"),
    ]

    operations = [
        migrations.AddField(
            model_name="devicesnmpconfig",
            name="vlan_subinterface_inference",
            field=models.CharField(
                blank=True,
                choices=[("auto", "Auto"), ("enabled", "Enabled"), ("disabled", "Disabled")],
                help_text="Override dot-suffix VLAN inference for this device. Global = use global setting.",
                max_length=10,
                verbose_name="Infer VLANs from subinterfaces",
            ),
        ),
        migrations.AddField(
            model_name="snmpsyncconfig",
            name="vlan_subinterface_inference",
            field=models.CharField(
                choices=[("auto", "Auto"), ("enabled", "Enabled"), ("disabled", "Disabled")],
                default="auto",
                help_text=(
                    "Controls whether interface names like parent.30 are treated as VLAN 30. "
                    "Auto enables this for MikroTik only."
                ),
                max_length=10,
                verbose_name="Infer VLANs from subinterfaces",
            ),
        ),
    ]

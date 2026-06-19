from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_snmp_sync", "0012_syncrun_vlan_counters"),
    ]

    operations = [
        migrations.CreateModel(
            name="SyncRunChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=20)),
                ("object_type", models.CharField(max_length=50)),
                ("object_repr", models.CharField(max_length=200)),
                ("field", models.CharField(blank=True, max_length=80)),
                ("old_value", models.TextField(blank=True)),
                ("new_value", models.TextField(blank=True)),
                ("message", models.TextField(blank=True)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="changes",
                        to="netbox_snmp_sync.syncrun",
                    ),
                ),
            ],
            options={
                "ordering": ("pk",),
            },
        ),
    ]

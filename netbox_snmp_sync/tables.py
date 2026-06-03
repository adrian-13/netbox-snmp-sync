import django_tables2 as tables

from netbox.tables import NetBoxTable, columns

from .models import DeviceSNMPConfig, SyncRun


class DeviceSNMPConfigTable(NetBoxTable):
    device = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()
    snmp_version = columns.ChoiceFieldColumn()

    class Meta(NetBoxTable.Meta):
        model = DeviceSNMPConfig
        fields = (
            "pk",
            "id",
            "device",
            "enabled",
            "snmp_version",
            "port",
            "community",
            "timeout",
            "retries",
            "created",
            "last_updated",
        )
        default_columns = ("device", "enabled", "snmp_version", "port", "community")


class SyncRunTable(NetBoxTable):
    device = tables.Column(linkify=True)
    trigger = columns.ChoiceFieldColumn()
    mode = columns.ChoiceFieldColumn()
    status = columns.ChoiceFieldColumn()
    # SyncRun is read-only history (no edit view) — only expose delete.
    actions = columns.ActionsColumn(actions=("delete",))

    class Meta(NetBoxTable.Meta):
        model = SyncRun
        fields = (
            "pk",
            "id",
            "created",
            "device",
            "trigger",
            "mode",
            "status",
            "interfaces_created",
            "interfaces_updated",
            "interfaces_existing",
            "ips_created",
            "ips_existing",
            "reverted",
            "message",
        )
        default_columns = (
            "created",
            "device",
            "trigger",
            "mode",
            "status",
            "interfaces_created",
            "ips_created",
            "reverted",
        )

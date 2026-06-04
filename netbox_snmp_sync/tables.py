import django_tables2 as tables

from netbox.tables import NetBoxTable, columns

from .models import DeviceSNMPConfig, SyncRun


SNMP_TEST_BUTTON = """
<a href="{% url 'plugins:netbox_snmp_sync:devicesnmpconfig_test' pk=record.pk %}"
   class="btn btn-sm btn-cyan" title="Test SNMP (read-only)">
  <i class="mdi mdi-access-point-check"></i>
</a>
"""


LAST_TEST_COL = """{% if record.last_test_time %}<span class="badge text-bg-{{ record.last_test_color }}">{% if record.last_tested_ok %}OK{% else %}Failed{% endif %}</span> <span class="text-muted" title="{{ record.last_test_message }}">{{ record.last_test_time|date:'Y-m-d H:i' }}</span>{% else %}<span class="text-muted">never</span>{% endif %}"""


class DeviceSNMPConfigTable(NetBoxTable):
    device = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()
    snmp_version = columns.ChoiceFieldColumn()
    last_test = tables.TemplateColumn(template_code=LAST_TEST_COL, verbose_name="Last test", orderable=False)
    actions = columns.ActionsColumn(extra_buttons=SNMP_TEST_BUTTON)

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
            "last_test",
            "created",
            "last_updated",
        )
        default_columns = ("device", "enabled", "snmp_version", "port", "community", "last_test")


class SyncRunTable(NetBoxTable):
    created = columns.DateTimeColumn(linkify=True)  # click the timestamp to open the run (Revert lives there)
    device = tables.Column(linkify=True)
    trigger = columns.ChoiceFieldColumn()
    mode = columns.ChoiceFieldColumn()
    status = columns.ChoiceFieldColumn()
    reverted = columns.BooleanColumn()
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

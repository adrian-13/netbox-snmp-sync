import django_tables2 as tables

from netbox.tables import NetBoxTable, columns

from .models import DeviceSNMPConfig, SyncRun


SNMP_TEST_BUTTON = """
<button type="submit"
        class="btn btn-sm btn-cyan"
        title="Test SNMP"
        formaction="{% url 'plugins:netbox_snmp_sync:devicesnmpconfig_test' pk=record.pk %}"
        formmethod="post"
        formnovalidate>
  <i class="mdi mdi-access-point-check"></i>
</button>
"""


LAST_TEST_COL = """{% if record.last_test_time %}<span class="badge text-bg-{{ record.last_test_color }}">{% if record.last_tested_ok %}OK{% else %}Failed{% endif %}</span> <span class="text-muted" title="{{ record.last_test_message }}">{{ record.last_test_time|date:'Y-m-d H:i' }}</span>{% else %}<span class="text-muted">never</span>{% endif %}"""

LAST_SYNC_COL = """{% if record.last_sync_at %}<span class="badge text-bg-{{ record.last_sync_color }}">{{ record.get_last_sync_status_display }}</span> <span class="text-muted" title="{{ record.last_sync_message }}">{{ record.last_sync_at|date:'Y-m-d H:i' }}</span>{% else %}<span class="text-muted">never</span>{% endif %}"""

SYNC_STATE_COL = """<span class="badge text-bg-{{ record.sync_state_color }}" title="{{ record.last_sync_message }}">{{ record.sync_state_label }}</span>{% if record.sync_queued_at %} <span class="text-muted">{{ record.sync_queued_at|date:'Y-m-d H:i' }}</span>{% endif %}{% if record.is_retrying %} <span class="text-muted">failures: {{ record.consecutive_sync_failures }}</span>{% endif %}"""

NEXT_SYNC_COL = """{% if not record.enabled %}<span class="text-muted">disabled</span>{% elif record.sync_state == 'disabled' %}<span class="text-muted">not scheduled</span>{% elif record.sync_state == 'retry_due' %}<span class="badge text-bg-orange">Retry due</span> <span class="text-muted">{{ record.next_sync_at|date:'Y-m-d H:i' }}</span>{% elif record.sync_state == 'retry' %}<span class="badge text-bg-red">Retry</span> <span class="text-muted">{{ record.next_sync_at|date:'Y-m-d H:i' }}</span>{% elif record.sync_state == 'due' %}<span class="badge text-bg-orange">Due</span> <span class="text-muted">{{ record.next_sync_at|date:'Y-m-d H:i' }}</span>{% elif record.next_sync_at %}{{ record.next_sync_at|date:'Y-m-d H:i' }}{% else %}<span class="text-muted">pending</span>{% endif %}"""

SCHEDULE_COL = """<span class="badge text-bg-{{ record.schedule_color }}">{{ record.schedule_label }}</span>"""

BEHAVIOUR_COL = """<span title="Interfaces">{{ record.sync_interfaces_label }}</span> / <span title="IP addresses">{{ record.sync_ip_addresses_label }}</span> / <span title="VLAN writes">{{ record.write_vlans_label }}</span>"""


class DeviceSNMPConfigTable(NetBoxTable):
    device = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()
    snmp_version = columns.ChoiceFieldColumn()
    last_sync = tables.TemplateColumn(template_code=LAST_SYNC_COL, verbose_name="Last sync", orderable=False)
    schedule = tables.TemplateColumn(template_code=SCHEDULE_COL, verbose_name="Schedule", orderable=False)
    behaviour = tables.TemplateColumn(template_code=BEHAVIOUR_COL, verbose_name="Sync", orderable=False)
    sync_state = tables.TemplateColumn(template_code=SYNC_STATE_COL, verbose_name="Sync state", orderable=False)
    next_sync = tables.TemplateColumn(template_code=NEXT_SYNC_COL, verbose_name="Next sync", orderable=False)
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
            "schedule",
            "behaviour",
            "last_sync",
            "sync_state",
            "next_sync",
            "created",
            "last_updated",
        )
        default_columns = (
            "device", "enabled", "snmp_version", "port", "community",
            "behaviour", "schedule", "last_sync", "sync_state", "next_sync",
        )


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

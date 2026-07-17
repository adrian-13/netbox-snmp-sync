import django_filters

from dcim.models import Device, Site
from netbox.filtersets import NetBoxModelFilterSet

from .choices import SNMPVersionChoices
from .models import DeviceSNMPConfig, SyncRun


class DeviceSNMPConfigFilterSet(NetBoxModelFilterSet):
    device_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Device.objects.all(),
        label="Device (ID)",
    )
    site_id = django_filters.ModelMultipleChoiceFilter(
        field_name="device__site",
        queryset=Site.objects.all(),
        label="Site (ID)",
    )
    snmp_version = django_filters.MultipleChoiceFilter(choices=SNMPVersionChoices)

    class Meta:
        model = DeviceSNMPConfig
        fields = (
            "id", "device", "enabled", "port", "rename_device_to_sysname",
            "sync_interfaces", "sync_ip_addresses", "update_existing", "set_mac_address",
            "write_vlans", "create_vlans", "vlan_subinterface_inference",
            "sync_interval_minutes", "sync_at_hours",
            "last_sync_status", "next_sync_at",
        )

    def search(self, queryset, name, value):
        return queryset.filter(device__name__icontains=value)


class SyncRunFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = SyncRun
        fields = ("id", "device", "trigger", "mode", "status")

    def search(self, queryset, name, value):
        return queryset.filter(message__icontains=value)

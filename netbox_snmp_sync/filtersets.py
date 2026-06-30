from netbox.filtersets import NetBoxModelFilterSet

from .models import DeviceSNMPConfig, SyncRun


class DeviceSNMPConfigFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = DeviceSNMPConfig
        fields = (
            "id", "device", "enabled", "snmp_version", "port", "rename_device_to_sysname",
            "sync_interfaces", "sync_ip_addresses", "update_existing", "set_mac_address",
            "write_vlans", "create_vlans", "vlan_subinterface_inference",
            "sync_interval_hours", "sync_at_hours",
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

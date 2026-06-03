from netbox.filtersets import NetBoxModelFilterSet

from .models import DeviceSNMPConfig, SyncRun


class DeviceSNMPConfigFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = DeviceSNMPConfig
        fields = ("id", "device", "enabled", "snmp_version", "port")

    def search(self, queryset, name, value):
        return queryset.filter(device__name__icontains=value)


class SyncRunFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = SyncRun
        fields = ("id", "device", "trigger", "mode", "status")

    def search(self, queryset, name, value):
        return queryset.filter(message__icontains=value)

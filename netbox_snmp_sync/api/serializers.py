from dcim.api.serializers import DeviceSerializer
from netbox.api.serializers import NetBoxModelSerializer

from ..models import DeviceSNMPConfig, SyncRun


class DeviceSNMPConfigSerializer(NetBoxModelSerializer):
    device = DeviceSerializer(nested=True)

    class Meta:
        model = DeviceSNMPConfig
        fields = (
            "id", "url", "display", "device", "enabled",
            "snmp_version", "port", "community",
            "username", "auth_protocol", "auth_key", "priv_protocol", "priv_key",
            "timeout", "retries", "target_override", "default_ethernet_type", "skip_loopback_ips",
            "tags", "custom_fields", "created", "last_updated",
        )
        brief_fields = ("id", "url", "display", "device", "snmp_version")
        # SNMP secrets: settable via the API but never returned in responses.
        extra_kwargs = {
            "community": {"write_only": True},
            "auth_key": {"write_only": True},
            "priv_key": {"write_only": True},
        }


class SyncRunSerializer(NetBoxModelSerializer):
    device = DeviceSerializer(nested=True, allow_null=True)

    class Meta:
        model = SyncRun
        fields = (
            "id", "url", "display", "device", "trigger", "mode", "status",
            "interfaces_created", "interfaces_updated", "interfaces_existing", "interfaces_ignored",
            "ips_created", "ips_existing", "reverted", "message",
            "tags", "custom_fields", "created", "last_updated",
        )
        brief_fields = ("id", "url", "display", "device", "mode", "status")

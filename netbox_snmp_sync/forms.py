from django import forms

from dcim.models import Device
from netbox.forms import NetBoxModelForm
from utilities.forms.fields import DynamicModelChoiceField, DynamicModelMultipleChoiceField

from .choices import SNMPVersionChoices
from .models import DeviceSNMPConfig


class DeviceSNMPConfigForm(NetBoxModelForm):
    device = DynamicModelChoiceField(queryset=Device.objects.all())

    class Meta:
        model = DeviceSNMPConfig
        fields = (
            "device",
            "enabled",
            "snmp_version",
            "port",
            "community",
            "username",
            "auth_protocol",
            "auth_key",
            "priv_protocol",
            "priv_key",
            "timeout",
            "retries",
            "target_override",
            "default_ethernet_type",
            "skip_loopback_ips",
            "tags",
        )


class BulkSNMPConfigForm(forms.Form):
    """Create SNMP configs for many devices at once (optionally pulling each device's
    community from a custom field, like the standalone tool's 'Import from NetBox')."""

    devices = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(),
        label="Devices",
    )
    snmp_version = forms.ChoiceField(choices=SNMPVersionChoices, initial=SNMPVersionChoices.V2C)
    community = forms.CharField(
        required=False,
        help_text="Default community (v1/v2c). Overridden per device by the custom field below, if set.",
    )
    port = forms.IntegerField(initial=161, min_value=1, max_value=65535)
    community_custom_field = forms.CharField(
        required=False,
        label="Community custom field",
        help_text="Name of a device custom field holding the SNMP community; used per device when present.",
    )
    overwrite = forms.BooleanField(
        required=False,
        label="Overwrite existing",
        help_text="Update devices that already have an SNMP config (default: skip them).",
    )

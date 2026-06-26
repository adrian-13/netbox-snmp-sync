from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from dcim.models import Device
from netbox.forms import NetBoxModelBulkEditForm, NetBoxModelForm, NetBoxModelImportForm
from utilities.forms import add_blank_choice
from utilities.forms.fields import (
    CSVModelChoiceField,
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
)
from utilities.forms.widgets import BulkEditNullBooleanSelect

from .choices import SNMPVersionChoices
from .models import DeviceSNMPConfig, SNMPSyncConfig, normalize_sync_hours


class SyncHoursFormMixin:
    def clean_sync_at_hours(self):
        raw = (self.cleaned_data.get("sync_at_hours") or "").strip()
        try:
            return normalize_sync_hours(raw)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc


class DeviceSNMPConfigForm(SyncHoursFormMixin, NetBoxModelForm):
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
            "rename_device_to_sysname",
            "sync_interval_hours",
            "sync_at_hours",
            "tags",
        )


class DeviceSNMPConfigBulkEditForm(SyncHoursFormMixin, NetBoxModelBulkEditForm):
    """Edit SNMP settings on many existing configs at once (Device SNMP Configs → Edit Selected)."""

    model = DeviceSNMPConfig

    enabled = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())
    snmp_version = forms.ChoiceField(choices=add_blank_choice(SNMPVersionChoices), required=False)
    port = forms.IntegerField(required=False, min_value=1, max_value=65535)
    community = forms.CharField(required=False)
    timeout = forms.FloatField(required=False)
    retries = forms.IntegerField(required=False, min_value=0)
    target_override = forms.CharField(required=False)
    skip_loopback_ips = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())
    rename_device_to_sysname = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())
    sync_interval_hours = forms.IntegerField(required=False, min_value=0)
    sync_at_hours = forms.CharField(required=False)

    nullable_fields = ("community", "target_override", "sync_interval_hours")


class DeviceSNMPConfigImportForm(SyncHoursFormMixin, NetBoxModelImportForm):
    device = CSVModelChoiceField(
        queryset=Device.objects.all(),
        to_field_name="name",
        help_text="Device name",
    )

    class Meta:
        model = DeviceSNMPConfig
        fields = (
            "device", "enabled", "snmp_version", "port", "community",
            "username", "auth_protocol", "auth_key", "priv_protocol", "priv_key",
            "timeout", "retries", "target_override", "default_ethernet_type", "skip_loopback_ips",
            "rename_device_to_sysname",
            "sync_interval_hours", "sync_at_hours",
        )


class SNMPSyncConfigForm(NetBoxModelForm):
    """Global plugin settings (singleton), editable in the UI."""

    class Meta:
        model = SNMPSyncConfig
        fields = (
            "sync_interval_hours", "sync_at_hours", "sync_job_timeout_seconds",
            "sync_stale_job_marker_minutes",
            "update_existing", "set_mac_address", "write_vlans", "create_vlans",
            "history_keep_days", "history_keep_count",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Tags are meaningless for a settings singleton.
        self.fields.pop("tags", None)

    def clean_sync_at_hours(self):
        """Validate and normalise the comma-separated hour list (0–23)."""
        raw = (self.cleaned_data.get("sync_at_hours") or "").strip()
        if not raw:
            return ""
        hours = []
        for part in raw.replace(" ", "").split(","):
            if not part:
                continue
            if not part.isdigit() or not (0 <= int(part) <= 23):
                raise forms.ValidationError(
                    f"'{part}' is not a valid hour. Use whole numbers 0–23, "
                    f"comma-separated (e.g. '3' or '3,15')."
                )
            hours.append(int(part))
        # Canonical form: unique, sorted.
        return ",".join(str(h) for h in sorted(set(hours)))

    def save(self, commit=True):
        schedule_changed = bool({"sync_interval_hours", "sync_at_hours"} & set(self.changed_data))
        obj = super().save(commit=commit)
        if commit and schedule_changed:
            DeviceSNMPConfig.reset_all_next_sync(timezone.now(), global_only=True)
        return obj


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

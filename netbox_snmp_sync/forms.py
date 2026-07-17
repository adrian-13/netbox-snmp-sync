from django import forms
from django.core.exceptions import ValidationError
from django.utils.html import format_html, format_html_join
from django.utils import timezone

from dcim.models import Device, Site
from ipam.models import VLANGroup
from netbox.forms import (
    NetBoxModelBulkEditForm,
    NetBoxModelFilterSetForm,
    NetBoxModelForm,
    NetBoxModelImportForm,
)
from utilities.forms import BOOLEAN_WITH_BLANK_CHOICES, add_blank_choice
from utilities.forms.fields import (
    CSVModelChoiceField,
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    TagFilterField,
)
from utilities.forms.rendering import FieldSet
from utilities.forms.widgets import BulkEditNullBooleanSelect

from .choices import SNMPVersionChoices, VlanSubinterfaceInferenceChoices
from .models import DeviceSNMPConfig, SNMPSyncConfig, normalize_sync_hours


NULLABLE_BOOLEAN_OVERRIDE_CHOICES = (
    ("", "Global"),
    ("true", "Enable"),
    ("false", "Disable"),
)

VLAN_INFERENCE_OVERRIDE_CHOICES = (
    ("", "Global"),
    (VlanSubinterfaceInferenceChoices.AUTO, "Auto"),
    (VlanSubinterfaceInferenceChoices.ENABLED, "Enabled"),
    (VlanSubinterfaceInferenceChoices.DISABLED, "Disabled"),
)

VLAN_INFERENCE_BULK_CHOICES = (
    ("", "---------"),
    (VlanSubinterfaceInferenceChoices.AUTO, "Auto"),
    (VlanSubinterfaceInferenceChoices.ENABLED, "Enabled"),
    (VlanSubinterfaceInferenceChoices.DISABLED, "Disabled"),
)


def coerce_nullable_boolean(value):
    if value in (True, "True", "true", "1", 1):
        return True
    if value in (False, "False", "false", "0", 0):
        return False
    return None


class SegmentedBooleanOverrideWidget(forms.RadioSelect):
    """Compact Global/On/Off selector for nullable per-device overrides."""

    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs or {}
        field_id = attrs.get("id") or f"id_{name}"
        selected = self._normalise_value(value)
        rows = []

        for index, (choice_value, label) in enumerate(self.choices):
            choice_id = f"{field_id}_{index}"
            rows.append((
                choice_id,
                name,
                choice_value,
                " checked" if choice_value == selected else "",
                self._button_class(choice_value),
                label,
            ))

        inputs = format_html_join(
            "",
            (
                '<input type="radio" class="btn-check" name="{}" id="{}" value="{}" autocomplete="off"{}>'
                '<label class="btn {}" for="{}">{}</label>'
            ),
            (
                (name, choice_id, choice_value, checked, button_class, choice_id, label)
                for choice_id, name, choice_value, checked, button_class, label in rows
            ),
        )
        return format_html(
            '<div class="d-block"><div class="btn-group btn-group-sm snmp-sync-override" role="group">{}</div></div>',
            inputs,
        )

    @staticmethod
    def _normalise_value(value):
        if value in (True, "True", "true", "1", 1):
            return "true"
        if value in (False, "False", "false", "0", 0):
            return "false"
        return ""

    @staticmethod
    def _button_class(value):
        return {
            "true": "btn-outline-success",
            "false": "btn-outline-danger",
        }.get(value, "btn-outline-secondary")


class NullableBooleanOverrideField(forms.TypedChoiceField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("choices", NULLABLE_BOOLEAN_OVERRIDE_CHOICES)
        kwargs.setdefault("coerce", coerce_nullable_boolean)
        kwargs.setdefault("empty_value", None)
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", SegmentedBooleanOverrideWidget)
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        return SegmentedBooleanOverrideWidget._normalise_value(value)


class SyncHoursFormMixin:
    def clean_sync_at_hours(self):
        raw = (self.cleaned_data.get("sync_at_hours") or "").strip()
        try:
            return normalize_sync_hours(raw)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc


class DeviceSNMPConfigForm(SyncHoursFormMixin, NetBoxModelForm):
    device = DynamicModelChoiceField(queryset=Device.objects.all())
    sync_interfaces = NullableBooleanOverrideField()
    sync_ip_addresses = NullableBooleanOverrideField()
    update_existing = NullableBooleanOverrideField()
    set_mac_address = NullableBooleanOverrideField()
    write_vlans = NullableBooleanOverrideField()
    create_vlans = NullableBooleanOverrideField()
    vlan_group = DynamicModelChoiceField(
        queryset=VLANGroup.objects.all(),
        required=False,
        help_text="Assign VLANs auto-created for this device to this group.",
    )
    vlan_subinterface_inference = forms.ChoiceField(
        choices=VLAN_INFERENCE_OVERRIDE_CHOICES,
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in (
            "sync_interfaces",
            "sync_ip_addresses",
            "update_existing",
            "set_mac_address",
            "write_vlans",
            "create_vlans",
        ):
            model_field = DeviceSNMPConfig._meta.get_field(name)
            self.fields[name].label = model_field.verbose_name.capitalize()
            self.fields[name].help_text = ""
        model_field = DeviceSNMPConfig._meta.get_field("vlan_subinterface_inference")
        self.fields["vlan_subinterface_inference"].label = model_field.verbose_name.capitalize()
        self.fields["vlan_subinterface_inference"].help_text = model_field.help_text

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
            "sync_interfaces",
            "sync_ip_addresses",
            "update_existing",
            "set_mac_address",
            "write_vlans",
            "create_vlans",
            "vlan_group",
            "vlan_subinterface_inference",
            "sync_interval_minutes",
            "sync_at_hours",
            "tags",
        )


class DeviceSNMPConfigFilterForm(NetBoxModelFilterSetForm):
    model = DeviceSNMPConfig
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet("device_id", "site_id", "enabled", "snmp_version", name="Device"),
        FieldSet(
            "sync_interfaces", "sync_ip_addresses", "update_existing", "set_mac_address",
            "write_vlans", "create_vlans",
            name="Sync behaviour",
        ),
    )

    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label="Device",
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(),
        required=False,
        label="Site",
    )
    enabled = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    snmp_version = forms.MultipleChoiceField(choices=SNMPVersionChoices, required=False)
    sync_interfaces = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    sync_ip_addresses = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    update_existing = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    set_mac_address = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    write_vlans = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    create_vlans = forms.NullBooleanField(required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES))
    tag = TagFilterField(DeviceSNMPConfig)


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
    sync_interfaces = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())
    sync_ip_addresses = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())
    update_existing = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())
    set_mac_address = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())
    write_vlans = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())
    create_vlans = forms.NullBooleanField(required=False, widget=BulkEditNullBooleanSelect())
    vlan_subinterface_inference = forms.ChoiceField(
        choices=VLAN_INFERENCE_BULK_CHOICES,
        required=False,
    )
    sync_interval_minutes = forms.IntegerField(required=False, min_value=0)
    sync_at_hours = forms.CharField(required=False)

    nullable_fields = (
        "community",
        "target_override",
        "sync_interfaces",
        "sync_ip_addresses",
        "update_existing",
        "set_mac_address",
        "write_vlans",
            "create_vlans",
            "vlan_subinterface_inference",
            "sync_interval_minutes",
    )


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
            "sync_interfaces", "sync_ip_addresses", "update_existing", "set_mac_address",
            "write_vlans", "create_vlans", "vlan_subinterface_inference",
            "sync_interval_minutes", "sync_at_hours",
        )


class SNMPSyncConfigForm(NetBoxModelForm):
    """Global plugin settings (singleton), editable in the UI."""

    class Meta:
        model = SNMPSyncConfig
        fields = (
            "sync_interval_minutes", "sync_at_hours", "sync_job_timeout_seconds",
            "sync_stale_job_marker_minutes", "sync_missed_schedule_grace_minutes",
            "sync_interfaces", "sync_ip_addresses",
            "update_existing", "set_mac_address", "write_vlans", "create_vlans",
            "vlan_subinterface_inference",
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
        schedule_changed = bool({"sync_interval_minutes", "sync_at_hours"} & set(self.changed_data))
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

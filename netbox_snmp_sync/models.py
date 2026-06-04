"""Database models for the SNMP Sync plugin.

``DeviceSNMPConfig`` holds the per-device SNMP connection settings (version, port,
community, SNMPv3 credentials, timeouts) that the user edits on the device. It can be
turned into a collector ``DeviceConfig`` spec — merged with plugin-level defaults — via
``to_spec()``.

Note: SNMP secrets (community, auth/priv keys) are stored in the database in clear text,
matching the standalone tool's config.yaml. Restrict access via NetBox permissions.
"""
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from netbox.models import NetBoxModel
from netbox.plugins import get_plugin_config

from .choices import (
    AuthProtocolChoices,
    PrivProtocolChoices,
    SNMPVersionChoices,
    SyncModeChoices,
    SyncStatusChoices,
    SyncTriggerChoices,
)
from .spec import DeviceConfig

PLUGIN_NAME = "netbox_snmp_sync"


class DeviceSNMPConfig(NetBoxModel):
    device = models.OneToOneField(
        to="dcim.Device",
        on_delete=models.CASCADE,
        related_name="snmp_config",
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Include this device in SNMP collection and scheduled syncs.",
    )
    snmp_version = models.CharField(
        max_length=4,
        choices=SNMPVersionChoices,
        default=SNMPVersionChoices.V2C,
    )
    port = models.PositiveIntegerField(default=161)
    # v1 / v2c
    community = models.CharField(
        max_length=255,
        blank=True,
        help_text="SNMP community string (SNMPv1 / v2c).",
    )
    # v3
    username = models.CharField(max_length=255, blank=True, verbose_name="SNMPv3 username")
    auth_protocol = models.CharField(
        max_length=10,
        choices=AuthProtocolChoices,
        default=AuthProtocolChoices.NONE,
        blank=True,
    )
    auth_key = models.CharField(max_length=255, blank=True, verbose_name="SNMPv3 auth key")
    priv_protocol = models.CharField(
        max_length=10,
        choices=PrivProtocolChoices,
        default=PrivProtocolChoices.NONE,
        blank=True,
    )
    priv_key = models.CharField(max_length=255, blank=True, verbose_name="SNMPv3 priv key")
    # transport
    timeout = models.FloatField(default=2.0)
    retries = models.PositiveSmallIntegerField(default=1)
    # optional behaviour overrides (blank → fall back to plugin default_settings)
    target_override = models.CharField(
        max_length=255,
        blank=True,
        help_text="Poll this host/IP instead of the device's primary IP.",
    )
    default_ethernet_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="NetBox interface type used when SNMP can't determine one. Blank = plugin default.",
    )
    skip_loopback_ips = models.BooleanField(default=True)
    # result of the last manual "Test SNMP" (set by the test view, not user-editable)
    last_test_time = models.DateTimeField(null=True, blank=True, editable=False)
    last_tested_ok = models.BooleanField(null=True, blank=True, editable=False)
    last_test_message = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ("device",)
        verbose_name = "device SNMP configuration"
        verbose_name_plural = "device SNMP configurations"

    def __str__(self):
        return f"SNMP config for {self.device}"

    @property
    def last_test_color(self):
        return "green" if self.last_tested_ok else "red"

    def get_absolute_url(self):
        return reverse("plugins:netbox_snmp_sync:devicesnmpconfig", args=[self.pk])

    def clean(self):
        super().clean()
        version = str(self.snmp_version)
        if version in ("1", "2c"):
            if not self.community:
                raise ValidationError({"community": "A community string is required for SNMPv1/v2c."})
        elif version == "3" and not self.username:
            raise ValidationError({"username": "A username is required for SNMPv3."})

    @property
    def target(self) -> str:
        """Resolved poll target: explicit override, else the device's primary IP."""
        if self.target_override:
            return self.target_override
        primary = self.device.primary_ip
        return str(primary.address.ip) if primary else ""

    def to_spec(self) -> DeviceConfig:
        """Build a collector ``DeviceConfig``, falling back to global settings where blank."""
        def default(key):
            return get_setting(key)

        return DeviceConfig(
            target=self.target,
            snmp_version=self.snmp_version or default("snmp_version"),
            snmp_community=self.community or default("snmp_community"),
            snmp_port=self.port or default("snmp_port"),
            timeout=self.timeout if self.timeout is not None else default("snmp_timeout"),
            retries=self.retries if self.retries is not None else default("snmp_retries"),
            snmp_user=self.username or None,
            snmp_auth_protocol=self.auth_protocol or default("snmp_auth_protocol"),
            snmp_auth_key=self.auth_key or None,
            snmp_priv_protocol=self.priv_protocol or default("snmp_priv_protocol"),
            snmp_priv_key=self.priv_key or None,
            default_ethernet_type=self.default_ethernet_type or default("default_ethernet_type"),
            skip_loopback_ips=self.skip_loopback_ips,
        )


class SyncRun(NetBoxModel):
    """A single SNMP sync run (history). Doubles as the source for the scheduler's
    per-device "is this device due?" check."""

    device = models.ForeignKey(
        to="dcim.Device",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="snmp_sync_runs",
    )
    trigger = models.CharField(max_length=20, choices=SyncTriggerChoices, default=SyncTriggerChoices.MANUAL)
    mode = models.CharField(max_length=20, choices=SyncModeChoices, default=SyncModeChoices.COMPARE)
    status = models.CharField(max_length=10, choices=SyncStatusChoices, default=SyncStatusChoices.OK)
    interfaces_created = models.PositiveIntegerField(default=0)
    interfaces_updated = models.PositiveIntegerField(default=0)
    interfaces_existing = models.PositiveIntegerField(default=0)
    interfaces_ignored = models.PositiveIntegerField(default=0)
    ips_created = models.PositiveIntegerField(default=0)
    ips_existing = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True)
    reverted = models.BooleanField(default=False)

    class Meta:
        ordering = ("-created",)
        verbose_name = "SNMP sync run"
        verbose_name_plural = "SNMP sync runs"

    def __str__(self):
        return f"{self.device} · {self.mode} · {self.get_status_display()}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_snmp_sync:syncrun", args=[self.pk])

    def get_status_color(self):
        return SyncStatusChoices.colors.get(self.status)

    def get_mode_color(self):
        return SyncModeChoices.colors.get(self.mode)

    def get_trigger_color(self):
        return SyncTriggerChoices.colors.get(self.trigger)

    @property
    def can_revert(self):
        return not self.reverted and self.created_objects.exists()

    def revert(self):
        """Delete the objects this run created (add-only → safe). Returns the number deleted.

        Deletes IPs first, then interfaces, then auto-created VLANs. A retry loop handles
        restricted/protected FKs (e.g. ``Interface.parent`` is ``RESTRICT``) by deleting
        child interfaces before their parents regardless of recorded order. Deletions run in
        the caller's request, so they land in NetBox's change log."""
        from django.db.models.deletion import ProtectedError, RestrictedError

        priority = {"ipaddress": 0, "interface": 1, "vlan": 2}
        rows = sorted(self.created_objects.all(), key=lambda r: priority.get(r.object_type.model, 9))
        pending = []
        for row in rows:
            model = row.object_type.model_class()
            if model is None:
                continue
            inst = model.objects.filter(pk=row.object_id).first()
            if inst is not None:
                pending.append(inst)

        deleted = 0
        progress = True
        while pending and progress:
            progress = False
            still = []
            for inst in pending:
                try:
                    inst.delete()
                    deleted += 1
                    progress = True
                except (RestrictedError, ProtectedError):
                    still.append(inst)  # blocked for now (e.g. a child still references it)
            pending = still

        self.reverted = True
        self.save()
        return deleted


class SyncRunObject(models.Model):
    """A single object created by a SyncRun — the basis for reverting that run."""

    run = models.ForeignKey(SyncRun, on_delete=models.CASCADE, related_name="created_objects")
    object_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    object_repr = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ("pk",)

    def __str__(self):
        return self.object_repr or f"{self.object_type} #{self.object_id}"


def record_created_objects(run: SyncRun, created_objects):
    """Persist the objects a sync created so the run can later be reverted."""
    for obj in created_objects:
        SyncRunObject.objects.create(
            run=run,
            object_type=ContentType.objects.get_for_model(type(obj)),
            object_id=obj.pk,
            object_repr=str(obj)[:200],
        )


class SNMPSyncConfig(NetBoxModel):
    """Singleton holding the plugin's global settings, editable in the UI (no restart needed).
    Seeded once from PLUGINS_CONFIG / default_settings on first access."""

    # scheduler
    sync_interval_hours = models.PositiveIntegerField(
        default=0, help_text="Hours between automatic syncs; 0 disables the interval scheduler "
                             "(unless specific hours are set below).")
    sync_at_hours = models.CharField(
        max_length=64, blank=True, verbose_name="Sync at hours",
        help_text="Run automatic syncs only at these hours of the day (0–23, comma-separated, "
                  "e.g. '3' or '3,15'). Blank = use the interval above at any hour. When set, "
                  "the interval is ignored and syncs run during these hours.")
    # sync behaviour
    update_existing = models.BooleanField(default=False, help_text="Also overwrite changed fields on existing interfaces.")
    set_mac_address = models.BooleanField(default=True)
    write_vlans = models.BooleanField(default=False, help_text="Write per-interface VLAN membership.")
    create_vlans = models.BooleanField(default=False, help_text="Auto-create missing VLANs in the device's site.")
    # history retention (prune job)
    history_keep_days = models.PositiveIntegerField(default=90)
    history_keep_count = models.PositiveIntegerField(default=1000)

    # Note: SNMP transport defaults (version/port/community/timeout/retries),
    # skip_loopback_ips and default_ethernet_type live per-device on DeviceSNMPConfig;
    # their ultimate fallback is the plugin's default_settings (via get_setting()).
    _SEED_FIELDS = (
        "sync_interval_hours", "sync_at_hours", "update_existing", "set_mac_address",
        "write_vlans", "create_vlans", "history_keep_days", "history_keep_count",
    )

    class Meta:
        verbose_name = "SNMP Sync settings"
        verbose_name_plural = "SNMP Sync settings"

    def __str__(self):
        return "SNMP Sync settings"

    def get_absolute_url(self):
        return reverse("plugins:netbox_snmp_sync:settings")

    @classmethod
    def get(cls):
        """Return the singleton, creating it (seeded from PLUGINS_CONFIG) on first access."""
        obj = cls.objects.first()
        if obj is None:
            seed = {}
            for fname in cls._SEED_FIELDS:
                try:
                    val = get_plugin_config(PLUGIN_NAME, fname)
                except Exception:
                    val = None
                if val is not None:
                    seed[fname] = val
            obj = cls.objects.create(**seed)
        return obj


def get_setting(name):
    """Read a global setting from the DB singleton; fall back to PLUGINS_CONFIG for keys
    that aren't stored on the model (e.g. SNMPv3 protocol defaults)."""
    cfg = SNMPSyncConfig.get()
    if hasattr(cfg, name):
        return getattr(cfg, name)
    return get_plugin_config(PLUGIN_NAME, name)

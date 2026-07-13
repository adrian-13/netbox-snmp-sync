"""Database models for the SNMP Sync plugin.

``DeviceSNMPConfig`` holds the per-device SNMP connection settings (version, port,
community, SNMPv3 credentials, timeouts) that the user edits on the device. It can be
turned into a collector ``DeviceConfig`` spec — merged with plugin-level defaults — via
``to_spec()``.

Note: SNMP secrets (community, auth/priv keys) are stored in the database in clear text,
matching the standalone tool's config.yaml. Restrict access via NetBox permissions.
"""
from datetime import timedelta
from uuid import UUID, uuid4

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from netbox.models import NetBoxModel
from netbox.plugins import get_plugin_config

from .choices import (
    AuthProtocolChoices,
    PrivProtocolChoices,
    SNMPVersionChoices,
    SyncModeChoices,
    SyncStatusChoices,
    SyncTriggerChoices,
    VlanSubinterfaceInferenceChoices,
)
from .spec import DeviceConfig

PLUGIN_NAME = "netbox_snmp_sync"
SCHEDULE_SPREAD_MAX_MINUTES = 15
STALE_SYNC_JOB_MARKER_HOURS = 2
STALE_SYNC_JOB_MARKER_MESSAGE = "Cleared stale SNMP sync marker after worker restart or lost job."
MISSED_SCHEDULE_GRACE_MINUTES = 360
MISSED_SCHEDULE_MESSAGE = "Re-anchored missed SNMP sync schedule after scheduler downtime."
ACTIVE_JOB_STATUSES = {"pending", "scheduled", "running"}


SYNC_BEHAVIOUR_FIELDS = (
    "sync_interfaces",
    "sync_ip_addresses",
    "update_existing",
    "set_mac_address",
    "write_vlans",
    "create_vlans",
)


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
    rename_device_to_sysname = models.BooleanField(
        default=False,
        verbose_name="Rename device to sysName",
        help_text="When applying SNMP sync, rename the NetBox device to the collected SNMP sysName.",
    )
    sync_interfaces = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Sync interfaces",
        help_text="Override interface create/update sync for this device. Global = use global setting.",
    )
    sync_ip_addresses = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Sync IP addresses",
        help_text="Override IP address sync for this device. Global = use global setting.",
    )
    update_existing = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Update existing objects",
        help_text="Override updating existing interfaces for this device. Global = use global setting.",
    )
    set_mac_address = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Set MAC address",
        help_text="Override primary MAC address writes for this device. Global = use global setting.",
    )
    write_vlans = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Write VLANs",
        help_text="Override per-interface VLAN membership writes for this device. Global = use global setting.",
    )
    create_vlans = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Create VLANs",
        help_text="Override automatic VLAN creation for this device. Global = use global setting.",
    )
    vlan_group = models.ForeignKey(
        to="ipam.VLANGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="VLAN group",
        help_text="Assign VLANs auto-created for this device to this group. Blank = no group.",
    )
    vlan_subinterface_inference = models.CharField(
        max_length=10,
        blank=True,
        choices=VlanSubinterfaceInferenceChoices,
        verbose_name="Infer VLANs from subinterfaces",
        help_text="Override dot-suffix VLAN inference for this device. Global = use global setting.",
    )
    sync_interval_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Per-device hours between automatic syncs. Blank = use global setting; 0 disables interval sync "
                  "for this device unless Sync at hours is set.",
    )
    sync_at_hours = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Sync at hours",
        help_text="Per-device sync hours (0-23, comma-separated). Blank = use global setting unless a per-device "
                  "interval is set.",
    )
    # result of the last manual "Test SNMP" (set by the test view, not user-editable)
    last_test_time = models.DateTimeField(null=True, blank=True, editable=False)
    last_tested_ok = models.BooleanField(null=True, blank=True, editable=False)
    last_test_message = models.CharField(max_length=255, blank=True, editable=False)
    # scheduler state; SyncRun remains the immutable history, these fields make scheduling visible and deterministic
    last_sync_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_sync_status = models.CharField(
        max_length=10, choices=SyncStatusChoices, blank=True, editable=False
    )
    last_sync_message = models.CharField(max_length=255, blank=True, editable=False)
    next_sync_at = models.DateTimeField(null=True, blank=True, editable=False)
    consecutive_sync_failures = models.PositiveSmallIntegerField(default=0, editable=False)
    sync_job_id = models.UUIDField(null=True, blank=True, editable=False)
    sync_queued_at = models.DateTimeField(null=True, blank=True, editable=False)
    sync_started_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ("device",)
        verbose_name = "device SNMP configuration"
        verbose_name_plural = "device SNMP configurations"

    def __str__(self):
        return f"SNMP config for {self.device}"

    @property
    def last_test_color(self):
        return "green" if self.last_tested_ok else "red"

    @property
    def last_sync_color(self):
        return SyncStatusChoices.colors.get(self.last_sync_status)

    @property
    def is_sync_due(self):
        return bool(self.enabled and self.next_sync_at and self.next_sync_at <= timezone.now())

    @property
    def is_retrying(self):
        return self.last_sync_status == SyncStatusChoices.FAILED and self.consecutive_sync_failures > 0

    @property
    def sync_state(self):
        if self.sync_started_at:
            return "running"
        if self.sync_queued_at:
            return "queued"
        if not self.is_schedule_enabled():
            return "disabled"
        if self.is_retrying:
            return "retry_due" if self.is_sync_due else "retry"
        if self.is_sync_due:
            return "due"
        return "waiting"

    @property
    def sync_state_label(self):
        return {
            "running": "Running",
            "queued": "Queued",
            "retry_due": "Retry due",
            "retry": "Retry",
            "due": "Due",
            "disabled": "Disabled",
            "waiting": "Waiting",
        }.get(self.sync_state, "Waiting")

    @property
    def sync_state_color(self):
        return {
            "running": "blue",
            "queued": "cyan",
            "retry_due": "orange",
            "retry": "red",
            "due": "orange",
            "disabled": "gray",
            "waiting": "gray",
        }.get(self.sync_state, "gray")

    @property
    def schedule_label(self):
        if self.sync_at_hours:
            return f"Hours {self.sync_at_hours}"
        if self.sync_interval_hours is not None:
            if self.sync_interval_hours == 0:
                return "Disabled"
            return f"Interval {self.sync_interval_hours}h"
        return "Global"

    @property
    def schedule_color(self):
        if self.sync_at_hours:
            return "purple"
        if self.sync_interval_hours is not None:
            if self.sync_interval_hours == 0:
                return "gray"
            return "blue"
        return "gray"

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
        try:
            self.sync_at_hours = normalize_sync_hours(self.sync_at_hours)
        except ValidationError as exc:
            raise ValidationError({"sync_at_hours": exc.messages}) from exc

    def save(self, *args, **kwargs):
        self.sync_at_hours = normalize_sync_hours(self.sync_at_hours)
        old = None
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values(
                "enabled", "sync_interval_hours", "sync_at_hours",
            ).first()

        super().save(*args, **kwargs)

        created_without_next_sync = old is None and self.next_sync_at is None
        schedule_changed = old is not None and any(
            old[field] != getattr(self, field)
            for field in ("enabled", "sync_interval_hours", "sync_at_hours")
        )
        if created_without_next_sync or schedule_changed:
            self.reset_next_sync(timezone.now())

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
            vlan_subinterface_inference=self.get_effective_vlan_subinterface_inference(),
        )

    def get_effective_sync_behaviour(self):
        """Return write/compare behaviour after applying per-device overrides."""
        return {
            field: bool(get_setting(field) if getattr(self, field) is None else getattr(self, field))
            for field in SYNC_BEHAVIOUR_FIELDS
        }

    def get_sync_behaviour_label(self, field):
        value = getattr(self, field)
        if value is None:
            return f"Global ({'Yes' if bool(get_setting(field)) else 'No'})"
        return "Yes" if value else "No"

    @property
    def sync_interfaces_label(self):
        return self.get_sync_behaviour_label("sync_interfaces")

    @property
    def sync_ip_addresses_label(self):
        return self.get_sync_behaviour_label("sync_ip_addresses")

    @property
    def update_existing_label(self):
        return self.get_sync_behaviour_label("update_existing")

    @property
    def set_mac_address_label(self):
        return self.get_sync_behaviour_label("set_mac_address")

    @property
    def write_vlans_label(self):
        return self.get_sync_behaviour_label("write_vlans")

    @property
    def create_vlans_label(self):
        return self.get_sync_behaviour_label("create_vlans")

    def get_effective_vlan_subinterface_inference(self):
        return self.vlan_subinterface_inference or get_setting("vlan_subinterface_inference") or VlanSubinterfaceInferenceChoices.AUTO

    @property
    def vlan_subinterface_inference_label(self):
        if self.vlan_subinterface_inference:
            return self.get_vlan_subinterface_inference_display()
        value = self.get_effective_vlan_subinterface_inference()
        label = next(
            (choice[1] for choice in VlanSubinterfaceInferenceChoices.CHOICES if choice[0] == value),
            value,
        )
        return f"Global ({label})"

    def get_effective_sync_interval_hours(self):
        if self.sync_interval_hours is not None:
            return self.sync_interval_hours
        return get_setting("sync_interval_hours") or 0

    def get_effective_sync_at_hours(self):
        if self.sync_at_hours:
            return self.sync_at_hours
        if self.sync_interval_hours is not None:
            return ""
        return get_setting("sync_at_hours") or ""

    def get_allowed_sync_hours(self):
        return parse_sync_hours(self.get_effective_sync_at_hours())

    def is_schedule_enabled(self):
        return bool(self.enabled and (self.get_effective_sync_interval_hours() > 0 or self.get_allowed_sync_hours()))

    def uses_global_schedule(self):
        return self.sync_interval_hours is None and not self.sync_at_hours

    def is_missed_schedule(self, reference=None):
        """Return whether a due schedule is old enough to skip as scheduler downtime.

        Short downtime should still catch up by queueing the sync. Once the due timestamp is
        much older than the configured grace window, re-anchor it so a restarted scheduler does
        not keep showing yesterday's missed run or stampede every device at once.
        """
        reference = reference or timezone.now()
        grace_minutes = get_missed_schedule_grace_minutes()
        if grace_minutes <= 0:
            return False
        if (
            not self.enabled
            or not self.is_schedule_enabled()
            or not self.next_sync_at
            or self.sync_job_id
            or self.is_retrying
            or not self.last_sync_at
            or self.last_sync_status != SyncStatusChoices.OK
        ):
            return False
        return self.next_sync_at < reference - timedelta(minutes=grace_minutes)

    def get_next_sync_time(self, reference=None, spread_offset=None):
        """Return the next scheduled sync time from a reference point, or None when disabled."""
        reference = reference or timezone.now()
        spread_offset = spread_offset or timedelta()
        hours = self.get_effective_sync_interval_hours()
        allowed_hours = self.get_allowed_sync_hours()

        if not self.is_schedule_enabled():
            return None

        if allowed_hours:
            local_ref = timezone.localtime(reference)
            candidates = []
            for hour in allowed_hours:
                candidate = local_ref.replace(hour=hour, minute=0, second=0, microsecond=0)
                if candidate <= local_ref:
                    candidate += timedelta(days=1)
                candidates.append(candidate)
            return min(candidates) + spread_offset

        return reference + timedelta(hours=hours) + spread_offset

    def get_retry_sync_time(self, reference=None):
        """Return the next retry time after a failed scheduled sync."""
        reference = reference or timezone.now()
        if self.get_allowed_sync_hours():
            return self.get_next_sync_time(reference)
        if self.get_effective_sync_interval_hours() <= 0:
            return None
        delay_hours = min(2 ** max(self.consecutive_sync_failures - 1, 0), 24)
        return reference + timedelta(hours=delay_hours)

    def reset_next_sync(self, reference=None, save=True, spread_offset=None):
        """Re-anchor this device's next scheduled sync to the current scheduler settings."""
        self.next_sync_at = self.get_next_sync_time(reference, spread_offset=spread_offset)
        if save and self.pk:
            self.save(update_fields=("next_sync_at",))
        return self.next_sync_at

    @classmethod
    def reset_all_next_sync(cls, reference=None, *, global_only=False):
        """Re-anchor all configs and spread enabled devices over a short deterministic window."""
        reference = reference or timezone.now()
        configs = list(cls.objects.select_related("device").order_by("device__name", "pk"))
        reset_configs = [
            config for config in configs
            if not global_only or config.uses_global_schedule()
        ]
        enabled_configs = [config for config in reset_configs if config.is_schedule_enabled()]
        spread_window = get_schedule_spread_window(enabled_configs)
        offsets = get_schedule_spread_offsets(len(enabled_configs), spread_window)
        offsets_by_pk = {
            config.pk: offset for config, offset in zip(enabled_configs, offsets)
        }

        for config in reset_configs:
            config.reset_next_sync(
                reference,
                spread_offset=offsets_by_pk.get(config.pk, timedelta()),
            )

    def clear_stale_sync_job(self, reference=None, save=True):
        """Clear a stuck queued/running marker once it is safe to enqueue a replacement.

        NetBox can retain an active-looking Job row after the worker/container dies, so an
        old local marker wins over the Job status. Fresh active jobs are still preserved.
        """
        reference = reference or timezone.now()
        if not self.sync_job_id:
            return False

        from core.models import Job

        timeout_minutes = get_stale_sync_job_marker_minutes()
        cutoff = reference - timedelta(minutes=timeout_minutes)
        marker_is_old = timeout_minutes > 0 and (
            (self.sync_started_at and self.sync_started_at < cutoff) or
            (self.sync_queued_at and self.sync_queued_at < cutoff)
        )
        job_status = Job.objects.filter(job_id=self.sync_job_id).values_list("status", flat=True).first()
        if job_status in ACTIVE_JOB_STATUSES:
            if marker_is_old:
                self._clear_stale_sync_marker(save=save)
                return True
            return False
        if job_status is not None:
            self.clear_sync_job(save=save)
            return True

        if marker_is_old:
            self._clear_stale_sync_marker(save=save)
            return True

        return False

    def _clear_stale_sync_marker(self, save=True):
        self.clear_sync_job(save=save)
        self.last_sync_message = STALE_SYNC_JOB_MARKER_MESSAGE
        if save and self.pk:
            type(self).objects.filter(pk=self.pk).update(last_sync_message=self.last_sync_message)

    def has_active_sync_job(self, reference=None):
        self.clear_stale_sync_job(reference)
        return bool(self.sync_job_id)

    def claim_sync_slot(self, reference=None):
        """Atomically reserve this config before enqueueing a sync job."""
        reference = reference or timezone.now()
        self.clear_stale_sync_job(reference)
        claim_id = uuid4()
        updated = type(self).objects.filter(pk=self.pk, sync_job_id__isnull=True).update(
            sync_job_id=claim_id,
            sync_queued_at=reference,
            sync_started_at=None,
        )
        if not updated:
            return None
        self.sync_job_id = claim_id
        self.sync_queued_at = reference
        self.sync_started_at = None
        return claim_id

    def mark_sync_queued(self, job_id, reference=None, save=True):
        self.sync_job_id = UUID(str(job_id))
        self.sync_queued_at = reference or timezone.now()
        if save and self.pk:
            type(self).objects.filter(pk=self.pk).update(
                sync_job_id=self.sync_job_id,
                sync_queued_at=self.sync_queued_at,
            )
            self.refresh_from_db(fields=("sync_job_id", "sync_queued_at", "sync_started_at"))

    def mark_sync_started(self, job_id=None, reference=None, save=True):
        reference = reference or timezone.now()
        if job_id:
            job_uuid = UUID(str(job_id))
            self.sync_job_id = job_uuid
        else:
            job_uuid = self.sync_job_id
        self.sync_started_at = reference
        if self.sync_queued_at is None:
            self.sync_queued_at = self.sync_started_at
        if save and self.pk:
            query = type(self).objects.filter(pk=self.pk)
            if job_uuid:
                query = query.filter(Q(sync_job_id=job_uuid) | Q(sync_job_id__isnull=True))
            updated = query.update(
                sync_job_id=job_uuid,
                sync_queued_at=self.sync_queued_at,
                sync_started_at=self.sync_started_at,
            )
            if not updated:
                self.refresh_from_db(fields=("sync_job_id", "sync_queued_at", "sync_started_at"))
                return False
            self.refresh_from_db(fields=("sync_job_id", "sync_queued_at", "sync_started_at"))
        return True

    def clear_sync_job(self, save=True, job_id=None):
        job_uuid = UUID(str(job_id)) if job_id else None
        self.sync_job_id = None
        self.sync_queued_at = None
        self.sync_started_at = None
        if save and self.pk:
            query = type(self).objects.filter(pk=self.pk)
            if job_uuid:
                query = query.filter(sync_job_id=job_uuid)
            updated = query.update(sync_job_id=None, sync_queued_at=None, sync_started_at=None)
            if not updated:
                self.refresh_from_db(fields=("sync_job_id", "sync_queued_at", "sync_started_at"))
                return False
        return True

    def record_sync_result(self, run, *, update_schedule=False):
        """Mirror the latest sync result on the config and optionally advance the schedule."""
        self.last_sync_at = run.created
        self.last_sync_status = run.status
        self.last_sync_message = (run.message or "")[:255]

        if run.status == SyncStatusChoices.OK:
            self.consecutive_sync_failures = 0
            if update_schedule:
                self.next_sync_at = self.get_next_sync_time(run.created)
        else:
            self.consecutive_sync_failures += 1
            if update_schedule:
                self.next_sync_at = self.get_retry_sync_time(run.created)

        self.save(update_fields=(
            "last_sync_at", "last_sync_status", "last_sync_message",
            "next_sync_at", "consecutive_sync_failures",
        ))


class SyncRun(NetBoxModel):
    """A single SNMP sync run history row."""

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
    vlans_created = models.PositiveIntegerField(default=0)
    iface_vlans_set = models.PositiveIntegerField(default=0)
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


class SyncRunChange(models.Model):
    """A field-level change recorded for a sync run."""

    run = models.ForeignKey(SyncRun, on_delete=models.CASCADE, related_name="changes")
    action = models.CharField(max_length=20)
    object_type = models.CharField(max_length=50)
    object_repr = models.CharField(max_length=200)
    field = models.CharField(max_length=80, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ("pk",)

    def __str__(self):
        return f"{self.action} {self.object_type} {self.object_repr}"


def record_created_objects(run: SyncRun, created_objects):
    """Persist the objects a sync created so the run can later be reverted."""
    for obj in created_objects:
        SyncRunObject.objects.create(
            run=run,
            object_type=ContentType.objects.get_for_model(type(obj)),
            object_id=obj.pk,
            object_repr=str(obj)[:200],
        )


def record_sync_changes(run: SyncRun, changes):
    """Persist field-level changes produced by the sync engine."""
    for change in changes:
        SyncRunChange.objects.create(
            run=run,
            action=str(change.action)[:20],
            object_type=str(change.object_type)[:50],
            object_repr=str(change.object_repr)[:200],
            field=str(change.field or "")[:80],
            old_value=str(change.old_value or ""),
            new_value=str(change.new_value or ""),
            message=str(change.message or ""),
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
    sync_job_timeout_seconds = models.PositiveIntegerField(
        default=300,
        help_text="Maximum runtime for the SNMP collection phase of a background sync job. 0 disables this guard.",
    )
    sync_stale_job_marker_minutes = models.PositiveIntegerField(
        default=120,
        help_text="After this many minutes, a queued/running SNMP sync marker is considered stale and may be "
                  "cleared even if NetBox still shows the old job as active. Use 0 to disable automatic stale "
                  "marker cleanup for active-looking jobs.",
    )
    sync_missed_schedule_grace_minutes = models.PositiveIntegerField(
        default=MISSED_SCHEDULE_GRACE_MINUTES,
        help_text="If a successful device's next scheduled sync is overdue by more than this many minutes, "
                  "treat it as missed scheduler downtime and re-anchor it instead of queueing a catch-up sync. "
                  "Use 0 to always catch up overdue schedules.",
    )
    # sync behaviour
    sync_interfaces = models.BooleanField(default=True, help_text="Create and update interfaces from SNMP.")
    sync_ip_addresses = models.BooleanField(default=True, help_text="Create IP addresses from SNMP.")
    update_existing = models.BooleanField(default=False, help_text="Also overwrite changed fields on existing interfaces.")
    set_mac_address = models.BooleanField(default=True)
    write_vlans = models.BooleanField(default=False, help_text="Write per-interface VLAN membership.")
    create_vlans = models.BooleanField(default=False, help_text="Auto-create missing VLANs in the device's site.")
    vlan_subinterface_inference = models.CharField(
        max_length=10,
        choices=VlanSubinterfaceInferenceChoices,
        default=VlanSubinterfaceInferenceChoices.AUTO,
        verbose_name="Infer VLANs from subinterfaces",
        help_text="Controls whether interface names like parent.30 are treated as VLAN 30. Auto enables this for MikroTik only.",
    )
    # history retention (prune job)
    history_keep_days = models.PositiveIntegerField(default=90)
    history_keep_count = models.PositiveIntegerField(default=1000)

    # Note: SNMP transport defaults (version/port/community/timeout/retries),
    # skip_loopback_ips and default_ethernet_type live per-device on DeviceSNMPConfig;
    # their ultimate fallback is the plugin's default_settings (via get_setting()).
    _SEED_FIELDS = (
        "sync_interval_hours", "sync_at_hours", "sync_job_timeout_seconds", "sync_stale_job_marker_minutes",
        "sync_missed_schedule_grace_minutes",
        "sync_interfaces", "sync_ip_addresses", "update_existing", "set_mac_address", "write_vlans", "create_vlans",
        "vlan_subinterface_inference",
        "history_keep_days", "history_keep_count",
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


def get_stale_sync_job_marker_minutes():
    value = get_setting("sync_stale_job_marker_minutes")
    if value is None:
        return STALE_SYNC_JOB_MARKER_HOURS * 60
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return STALE_SYNC_JOB_MARKER_HOURS * 60


def get_missed_schedule_grace_minutes():
    value = get_setting("sync_missed_schedule_grace_minutes")
    if value is None:
        return MISSED_SCHEDULE_GRACE_MINUTES
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return MISSED_SCHEDULE_GRACE_MINUTES


def parse_sync_hours(raw):
    """Parse a comma-separated hour list ('3,15') into a set of valid ints in 0-23."""
    out = set()
    for part in str(raw or "").replace(" ", "").split(","):
        if part.isdigit() and 0 <= int(part) <= 23:
            out.add(int(part))
    return out


def normalize_sync_hours(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    hours = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        if not part.isdigit() or not (0 <= int(part) <= 23):
            raise ValidationError(
                f"'{part}' is not a valid hour. Use whole numbers 0-23, comma-separated."
            )
        hours.append(int(part))
    return ",".join(str(hour) for hour in sorted(set(hours)))


def get_schedule_spread_window(configs=None):
    """Return the window used when re-anchoring many device schedules at once."""
    if configs is not None:
        windows = []
        for config in configs:
            if config.get_allowed_sync_hours():
                windows.append(timedelta(minutes=SCHEDULE_SPREAD_MAX_MINUTES))
                continue
            hours = config.get_effective_sync_interval_hours()
            if hours > 0:
                windows.append(timedelta(minutes=min(SCHEDULE_SPREAD_MAX_MINUTES, max(hours * 60 - 1, 0))))
        return min(windows) if windows else timedelta()

    hours = get_setting("sync_interval_hours") or 0
    allowed_hours = parse_sync_hours(get_setting("sync_at_hours"))
    if hours <= 0 and not allowed_hours:
        return timedelta()

    if allowed_hours:
        return timedelta(minutes=SCHEDULE_SPREAD_MAX_MINUTES)

    return timedelta(minutes=min(SCHEDULE_SPREAD_MAX_MINUTES, max(hours * 60 - 1, 0)))


def get_schedule_spread_offsets(count, spread_window):
    """Evenly distribute count items from zero through spread_window."""
    if count <= 1 or spread_window <= timedelta():
        return [timedelta() for _ in range(max(count, 0))]

    step = spread_window / (count - 1)
    return [step * index for index in range(count)]

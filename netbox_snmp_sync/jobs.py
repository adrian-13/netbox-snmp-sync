"""Background jobs for SNMP collection + sync.

``SNMPSyncJob`` runs on demand from the device actions. ``ScheduledSNMPSyncJob`` is a
frequent scheduler check that queues one isolated sync job per due device, based on the
device's effective schedule. Every run is recorded as a ``SyncRun`` and mirrored onto
``DeviceSNMPConfig`` for visible scheduler state.
"""
import asyncio
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from core.choices import JobIntervalChoices
from core.exceptions import JobFailed
from netbox.context_managers import event_tracking
from netbox.jobs import JobRunner, system_job
from utilities.request import NetBoxFakeRequest

from . import engine
from .choices import SyncModeChoices, SyncStatusChoices, SyncTriggerChoices
from .snmp_collector import collect_with_ping

SCHEDULE_CHECK_INTERVAL_MINUTES = 5
SYSTEM_USERNAME = "netbox-snmp-sync"


def _system_user():
    """Return a local service user for scheduled writes so NetBox change logging has a username."""
    user_model = get_user_model()
    user, created = user_model.objects.get_or_create(
        username=SYSTEM_USERNAME,
        defaults={
            "email": "netbox-snmp-sync@example.invalid",
            "is_active": False,
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=("password",))
    return user


def _fake_request(user):
    """A minimal request so ORM writes inside a job land in NetBox's change log.

    NetBox's change-logging signals read ``request.id`` and ``request.user`` from the active
    request; jobs have none, so we synthesize one. Scheduled jobs use a disabled service user
    because NetBox's ObjectChange model requires a username."""
    user = user or _system_user()
    return NetBoxFakeRequest({
        "id": uuid.uuid4(),
        "user": user,
        "META": {},
        "POST": {},
        "GET": {},
        "path": "",
        "method": "POST",
    })


async def _collect_with_job_timeout(spec, timeout_seconds):
    if timeout_seconds and timeout_seconds > 0:
        try:
            return await asyncio.wait_for(collect_with_ping(spec), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"SNMP collection timed out after {timeout_seconds} seconds.") from exc
    return await collect_with_ping(spec)


def _sync_one(config, *, mode, trigger, logger=None, user=None, reset_schedule=False):
    """Poll one device over SNMP and compare or apply; record a SyncRun. Returns a summary.

    Writes are wrapped in ``event_tracking`` so they appear in NetBox's change log (audit).
    Raises ``JobFailed`` on a hard failure (no target / SNMP error) after recording a failed
    SyncRun, so callers can either let the job error (manual) or catch and continue (scheduled).
    """
    from .models import SyncRun, get_setting, record_created_objects

    device = config.device
    spec = config.to_spec()

    def _log(level, msg):
        if logger:
            getattr(logger, level)(msg)

    if not spec.target:
        run = SyncRun.objects.create(
            device=device, trigger=trigger, mode=mode, status=SyncStatusChoices.FAILED,
            message="No SNMP target (set a primary IP on the device or a target override).",
        )
        config.record_sync_result(run, update_schedule=(trigger == SyncTriggerChoices.SCHEDULED))
        raise JobFailed(f"{device}: no SNMP target.")

    _log("info", f"Polling {spec.target} over SNMP (v{spec.snmp_version}) …")
    try:
        data = asyncio.run(_collect_with_job_timeout(
            spec,
            get_setting("sync_job_timeout_seconds") or 0,
        ))
    except Exception as exc:  # noqa: BLE001
        run = SyncRun.objects.create(
            device=device, trigger=trigger, mode=mode, status=SyncStatusChoices.FAILED,
            message=f"SNMP collection failed: {exc}",
        )
        config.record_sync_result(run, update_schedule=(trigger == SyncTriggerChoices.SCHEDULED))
        raise JobFailed(f"{spec.target}: SNMP collection failed: {exc}") from exc

    _log("info", f"{spec.target}: sysName={data.sys_name}, "
                 f"{len(data.interfaces)} interfaces, {len(data.ip_addresses)} IPs")

    if mode in (SyncModeChoices.APPLY, SyncModeChoices.DRY_RUN):
        with event_tracking(_fake_request(user)):
            result = engine.apply_sync(
                device, data,
                dry_run=(mode == SyncModeChoices.DRY_RUN),
                update_existing=bool(get_setting("update_existing")),
                set_mac_address=bool(get_setting("set_mac_address")),
                write_vlans=bool(get_setting("write_vlans")),
                create_vlans=bool(get_setting("create_vlans")),
            )
        verb = "would create" if mode == SyncModeChoices.DRY_RUN else "created"
        summary = (f"{verb} {result.interfaces_created} interfaces, {result.ips_created} IPs; "
                   f"updated {result.interfaces_updated}, existing {result.interfaces_existing}, "
                   f"ignored {result.interfaces_ignored}; "
                   f"VLANs set {result.iface_vlans_set}, created {result.vlans_created}")
        msg = summary + (("; " + "; ".join(result.warnings)) if result.warnings else "")
        run = SyncRun.objects.create(
            device=device, trigger=trigger, mode=mode, status=SyncStatusChoices.OK,
            interfaces_created=result.interfaces_created, interfaces_updated=result.interfaces_updated,
            interfaces_existing=result.interfaces_existing, interfaces_ignored=result.interfaces_ignored,
            ips_created=result.ips_created, ips_existing=result.ips_existing,
            vlans_created=result.vlans_created, iface_vlans_set=result.iface_vlans_set,
            message=msg,
        )
        record_created_objects(run, result.created_objects)
        config.record_sync_result(
            run,
            update_schedule=(trigger == SyncTriggerChoices.SCHEDULED or reset_schedule),
        )
        _log("info", summary)
        for w in result.warnings:
            _log("warning", w)
        return summary

    # read-only compare
    diff = engine.compare_device(device, data)
    summary = (f"{diff.new_interfaces} new / {diff.changed_interfaces} changed interfaces, "
               f"{diff.new_ips} new IPs, {len(diff.netbox_only_interfaces)} only in NetBox")
    run = SyncRun.objects.create(
        device=device, trigger=trigger, mode=mode, status=SyncStatusChoices.OK, message=summary,
    )
    config.record_sync_result(
        run,
        update_schedule=(trigger == SyncTriggerChoices.SCHEDULED or reset_schedule),
    )
    _log("info", summary)
    return summary


class SNMPSyncJob(JobRunner):
    class Meta:
        name = "SNMP Sync"

    def run(self, *args, **kwargs):
        from .models import DeviceSNMPConfig

        mode = kwargs.get("mode", SyncModeChoices.COMPARE)
        trigger = kwargs.get("trigger", SyncTriggerChoices.MANUAL)
        reset_schedule = bool(kwargs.get("reset_schedule"))
        config = self.job.object
        if config is None:
            config = DeviceSNMPConfig.objects.get(pk=kwargs["config_pk"])
        if not config.mark_sync_started(self.job.job_id):
            raise JobFailed(f"{config.device}: another SNMP sync job is already active.")
        try:
            return _sync_one(config, mode=mode, trigger=trigger,
                             logger=self.logger, user=self.job.user,
                             reset_schedule=reset_schedule)
        finally:
            config.clear_sync_job(job_id=self.job.job_id)


@system_job(interval=SCHEDULE_CHECK_INTERVAL_MINUTES)
class ScheduledSNMPSyncJob(JobRunner):
    class Meta:
        name = "Scheduled SNMP Sync"

    def run(self, *args, **kwargs):
        from .models import DeviceSNMPConfig

        now = timezone.now()

        due = queued = skipped_active = 0
        for config in DeviceSNMPConfig.objects.filter(enabled=True).select_related("device"):
            if not config.target:
                continue
            if not config.is_schedule_enabled():
                if config.next_sync_at is not None:
                    config.reset_next_sync(now)
                continue
            if config.next_sync_at is None:
                config.reset_next_sync(now)
                if config.next_sync_at is None:
                    continue
            if config.next_sync_at and config.next_sync_at > now:
                continue
            due += 1
            if not config.claim_sync_slot(now):
                skipped_active += 1
                continue
            # Enqueue an isolated per-device job rather than syncing inline: one slow/hung
            # device no longer blocks the rest, failures are isolated, and the work spreads
            # across however many RQ workers are running.
            try:
                job = SNMPSyncJob.enqueue(
                    config_pk=config.pk,
                    mode=SyncModeChoices.APPLY,
                    trigger=SyncTriggerChoices.SCHEDULED,
                    user=self.job.user,
                )
                config.mark_sync_queued(job.job_id)
            except Exception:
                config.clear_sync_job()
                raise
            queued += 1

        self.logger.info(
            f"Scheduled SNMP sync: queued {queued}, "
            f"skipped {skipped_active} already active, {due} due device(s)."
        )


@system_job(interval=JobIntervalChoices.INTERVAL_DAILY)
class PruneSyncRunsJob(JobRunner):
    """Daily housekeeping: trim old SyncRun history per the plugin's retention settings."""

    class Meta:
        name = "Prune SNMP Sync history"

    def run(self, *args, **kwargs):
        from .models import SyncRun, get_setting

        keep_days = get_setting("history_keep_days") or 0
        keep_count = get_setting("history_keep_count") or 0
        before = SyncRun.objects.count()

        if keep_days > 0:
            cutoff = timezone.now() - timedelta(days=keep_days)
            SyncRun.objects.filter(created__lt=cutoff).delete()
        if keep_count > 0:
            keep_ids = list(SyncRun.objects.order_by("-created").values_list("pk", flat=True)[:keep_count])
            SyncRun.objects.exclude(pk__in=keep_ids).delete()

        pruned = before - SyncRun.objects.count()
        self.logger.info(f"Pruned {pruned} old sync run(s) (keep_days={keep_days}, keep_count={keep_count}).")

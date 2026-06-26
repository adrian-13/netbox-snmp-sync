import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import View

from netbox.views import generic
from utilities.views import register_model_view

from . import engine, filtersets, forms, tables
from .choices import SyncModeChoices, SyncStatusChoices, SyncTriggerChoices
from .dto import deserialize_device_data, serialize_device_data
from .jobs import SNMPSyncJob
from .models import DeviceSNMPConfig, SNMPSyncConfig, SyncRun, get_setting, record_created_objects, record_sync_changes
from .snmp_collector import collect_with_ping, quick_snmp_sys_name


def _collect_blocking(spec):
    """Run the async SNMP collector synchronously in a fresh thread.

    Using a dedicated thread guarantees there's no already-running event loop (which would
    break ``asyncio.run``), regardless of whether NetBox is served via WSGI or ASGI.
    """
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(collect_with_ping(spec))).result()


def _quick_sys_name_blocking(spec):
    """Run the fast SNMP sysName probe synchronously in a fresh thread."""
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(quick_snmp_sys_name(spec))).result()


def _vlan_preview_rows(data):
    rows = []
    for iface in data.interfaces.values():
        if not iface.access_vlan and not iface.tagged_vlans:
            continue
        rows.append({
            "name": iface.name,
            "access_vlan": iface.access_vlan,
            "tagged_vlans": ", ".join(str(v) for v in iface.tagged_vlans),
        })
    return rows


def _rename_device_to_sysname_pending(config, data):
    sys_name = (data.sys_name or "").strip()
    return bool(config.rename_device_to_sysname and sys_name and config.device.name != sys_name)


def _device_rename_messages(result):
    messages = []
    for change in getattr(result, "changes", ()):
        if change.object_type == "device" and change.field == "name":
            messages.append(f"renamed device {change.old_value} -> {change.new_value}")
    return messages


def _evaluate(spec):
    """Network-only SNMP probe of an already-resolved spec. Returns ``(ok, message)``.

    Touches no ORM state, so it is safe to run from a worker thread for bulk testing.
    """
    if not spec.target:
        return False, "No SNMP target (set a primary IP or target override)."
    t0 = time.monotonic()
    sys_name, err = _quick_sys_name_blocking(spec)
    elapsed = time.monotonic() - t0
    if err:
        return False, f"{spec.target}: {err}"
    return True, f"sysName={sys_name or '-'} ({elapsed:.1f}s)"


def _persist_test(config, ok, message):
    """Store the last-test result on the config (so the list column / device panel reflect it)
    and return a result dict for the result page."""
    config.last_test_time = timezone.now()
    config.last_tested_ok = ok
    config.last_test_message = message[:255]
    config.save()
    return {"device": config.device, "target": config.target, "ok": ok, "message": message}


def _run_test(config):
    """Resolve the spec (DB read), probe over SNMP, persist + return the result. Main-thread use."""
    ok, message = _evaluate(config.to_spec())
    return _persist_test(config, ok, message)


def _safe_referer(request):
    """Return the HTTP referer if it points back at this NetBox instance, else None."""
    ref = request.META.get("HTTP_REFERER")
    if ref and url_has_allowed_host_and_scheme(ref, allowed_hosts={request.get_host()}):
        return ref
    return None


@register_model_view(DeviceSNMPConfig, name="list", path="", detail=False)
class DeviceSNMPConfigListView(generic.ObjectListView):
    queryset = DeviceSNMPConfig.objects.all()
    table = tables.DeviceSNMPConfigTable
    filterset = filtersets.DeviceSNMPConfigFilterSet
    template_name = "netbox_snmp_sync/device_snmp_config_list.html"


@register_model_view(DeviceSNMPConfig)
class DeviceSNMPConfigView(generic.ObjectView):
    queryset = DeviceSNMPConfig.objects.all()


@register_model_view(DeviceSNMPConfig, name="add", detail=False)
@register_model_view(DeviceSNMPConfig, name="edit")
class DeviceSNMPConfigEditView(generic.ObjectEditView):
    queryset = DeviceSNMPConfig.objects.all()
    form = forms.DeviceSNMPConfigForm


@register_model_view(DeviceSNMPConfig, name="delete")
class DeviceSNMPConfigDeleteView(generic.ObjectDeleteView):
    queryset = DeviceSNMPConfig.objects.all()


@register_model_view(DeviceSNMPConfig, "bulk_import", detail=False)
class DeviceSNMPConfigBulkImportView(generic.BulkImportView):
    queryset = DeviceSNMPConfig.objects.all()
    model_form = forms.DeviceSNMPConfigImportForm


@register_model_view(DeviceSNMPConfig, "bulk_edit", path="edit", detail=False)
class DeviceSNMPConfigBulkEditView(generic.BulkEditView):
    queryset = DeviceSNMPConfig.objects.all()
    filterset = filtersets.DeviceSNMPConfigFilterSet
    table = tables.DeviceSNMPConfigTable
    form = forms.DeviceSNMPConfigBulkEditForm


@register_model_view(DeviceSNMPConfig, "bulk_delete", path="delete", detail=False)
class DeviceSNMPConfigBulkDeleteView(generic.BulkDeleteView):
    queryset = DeviceSNMPConfig.objects.all()
    filterset = filtersets.DeviceSNMPConfigFilterSet
    table = tables.DeviceSNMPConfigTable


@register_model_view(DeviceSNMPConfig, name="sync")
class DeviceSNMPConfigSyncView(LoginRequiredMixin, View):
    """Enqueue an SNMP collect/compare/sync job for a device's SNMP config."""

    def post(self, request, pk):
        return self._enqueue(request, pk)

    def _enqueue(self, request, pk):
        config = get_object_or_404(DeviceSNMPConfig, pk=pk)
        mode = request.POST.get("mode", SyncModeChoices.COMPARE)
        if mode not in {SyncModeChoices.COMPARE, SyncModeChoices.DRY_RUN, SyncModeChoices.APPLY}:
            messages.error(request, "Invalid SNMP sync mode.")
            return redirect(config.get_absolute_url())
        reset_schedule = request.POST.get("reset_schedule") == "1" and mode == SyncModeChoices.APPLY
        if not request.user.has_perm("netbox_snmp_sync.change_devicesnmpconfig"):
            messages.error(request, "You do not have permission to run SNMP sync.")
            return redirect(config.get_absolute_url())
        if not config.claim_sync_slot():
            messages.warning(request, f"SNMP sync for {config.device} is already queued or running.")
            return redirect(config.get_absolute_url())
        try:
            job = SNMPSyncJob.enqueue(
                config_pk=config.pk,
                user=request.user,
                mode=mode,
                reset_schedule=reset_schedule,
            )
            config.mark_sync_queued(job.job_id)
        except Exception:
            config.clear_sync_job()
            raise
        suffix = " and schedule reset" if reset_schedule else ""
        messages.success(request, f"Queued SNMP '{mode}' for {config.device}{suffix}.")
        return redirect(job.get_absolute_url())


@register_model_view(DeviceSNMPConfig, name="test", path="test")
class DeviceSNMPConfigTestView(LoginRequiredMixin, View):
    """Quick read-only SNMP connectivity test for one device: one sysName GET.

    Also persists the outcome on the config so the list column / device panel show it.
    Writes nothing else to NetBox.
    """

    def post(self, request, pk):
        config = get_object_or_404(DeviceSNMPConfig, pk=pk)
        if not request.user.has_perm("netbox_snmp_sync.change_devicesnmpconfig"):
            messages.error(request, "You do not have permission to run an SNMP test.")
            return redirect(config.get_absolute_url())

        result = _run_test(config)
        return render(request, "netbox_snmp_sync/test_result.html", {
            "results": [result],
            "ok_count": 1 if result["ok"] else 0,
            "fail_count": 0 if result["ok"] else 1,
            "return_url": _safe_referer(request) or config.device.get_absolute_url(),
        })


@register_model_view(DeviceSNMPConfig, "bulk_test", path="test", detail=False)
class DeviceSNMPConfigBulkTestView(LoginRequiredMixin, View):
    """Run a read-only SNMP test against every selected device's config at once and render a
    combined result page. Each device's last-test result is persisted too (Last test column).

    The probes run concurrently (bounded pool) and the collector quick-pings first, so even a
    large selection — including unreachable devices — finishes promptly instead of timing out.
    """

    list_url = "plugins:netbox_snmp_sync:devicesnmpconfig_list"

    def get(self, request):
        return redirect(self.list_url)

    def post(self, request):
        if not request.user.has_perm("netbox_snmp_sync.change_devicesnmpconfig"):
            messages.error(request, "You do not have permission to run an SNMP test.")
            return redirect(self.list_url)

        if request.POST.get("_all"):
            qs = DeviceSNMPConfig.objects.all()
        else:
            qs = DeviceSNMPConfig.objects.filter(pk__in=request.POST.getlist("pk"))
        configs = list(qs.select_related("device"))
        if not configs:
            messages.warning(request, "No SNMP configurations selected.")
            return redirect(self.list_url)

        # Resolve specs on the main thread (DB reads), probe concurrently (network I/O), then
        # persist serially on the main thread (ORM writes).
        specs = [(c, c.to_spec()) for c in configs]
        with ThreadPoolExecutor(max_workers=min(8, len(specs))) as ex:
            outcomes = list(ex.map(lambda pair: _evaluate(pair[1]), specs))

        results = [_persist_test(c, ok, msg) for (c, _spec), (ok, msg) in zip(specs, outcomes)]
        ok_count = sum(1 for r in results if r["ok"])
        return render(request, "netbox_snmp_sync/test_result.html", {
            "results": results,
            "ok_count": ok_count,
            "fail_count": len(results) - ok_count,
            "return_url": reverse(self.list_url),
        })


@register_model_view(DeviceSNMPConfig, "bulk_reconcile_markers", path="reconcile-markers", detail=False)
class DeviceSNMPConfigBulkReconcileMarkersView(LoginRequiredMixin, View):
    """Clear stale queued/running sync markers for selected SNMP configs."""

    list_url = "plugins:netbox_snmp_sync:devicesnmpconfig_list"

    def post(self, request):
        if not request.user.has_perm("netbox_snmp_sync.change_devicesnmpconfig"):
            messages.error(request, "You do not have permission to change SNMP scheduling.")
            return redirect(self.list_url)

        if request.POST.get("_all"):
            qs = DeviceSNMPConfig.objects.all()
        else:
            qs = DeviceSNMPConfig.objects.filter(pk__in=request.POST.getlist("pk"))
        configs = list(qs.select_related("device"))
        if not configs:
            messages.warning(request, "No SNMP configurations selected.")
            return redirect(self.list_url)

        now = timezone.now()
        cleared = 0
        active = 0
        no_marker = 0
        for config in configs:
            if not config.sync_job_id:
                no_marker += 1
                continue
            if config.clear_stale_sync_job(now):
                cleared += 1
            else:
                active += 1

        messages.success(
            request,
            f"Reconciled SNMP sync markers: cleared {cleared}, still active/recent {active}, no marker {no_marker}.",
        )
        return redirect(self.list_url)


@register_model_view(DeviceSNMPConfig, name="reset_schedule", path="reset-schedule")
class DeviceSNMPConfigResetScheduleView(LoginRequiredMixin, View):
    """Recalculate one config's next scheduled sync from its current effective schedule."""

    def post(self, request, pk):
        config = get_object_or_404(DeviceSNMPConfig, pk=pk)
        if not request.user.has_perm("netbox_snmp_sync.change_devicesnmpconfig"):
            messages.error(request, "You do not have permission to change SNMP scheduling.")
            return redirect(config.get_absolute_url())

        next_sync = config.reset_next_sync(timezone.now())
        if next_sync:
            messages.success(request, f"Recalculated next SNMP sync for {config.device}: {next_sync}.")
        else:
            messages.success(request, f"SNMP automatic sync is not scheduled for {config.device}.")
        return redirect(config.get_absolute_url())


@register_model_view(DeviceSNMPConfig, name="reconcile_marker", path="reconcile-marker")
class DeviceSNMPConfigReconcileMarkerView(LoginRequiredMixin, View):
    """Safely clear a stale sync marker when NetBox no longer has an active job for it."""

    def post(self, request, pk):
        config = get_object_or_404(DeviceSNMPConfig, pk=pk)
        if not request.user.has_perm("netbox_snmp_sync.change_devicesnmpconfig"):
            messages.error(request, "You do not have permission to change SNMP scheduling.")
            return redirect(config.get_absolute_url())

        if not config.sync_job_id:
            messages.info(request, f"{config.device} has no queued/running SNMP sync marker.")
            return redirect(config.get_absolute_url())

        marker = config.sync_job_id
        if config.clear_stale_sync_job(timezone.now()):
            messages.success(request, f"Cleared stale SNMP sync marker {marker} for {config.device}.")
        else:
            messages.warning(
                request,
                f"SNMP sync marker {marker} for {config.device} still matches an active or recent job.",
            )
        return redirect(config.get_absolute_url())


@register_model_view(DeviceSNMPConfig, name="preview", path="preview")
class DeviceSNMPConfigPreviewView(LoginRequiredMixin, View):
    """Interactive diff: poll the device over SNMP, show new/changed objects with checkboxes,
    and write only the selected ones (add-only) on submit."""

    template_name = "netbox_snmp_sync/preview.html"

    def get(self, request, pk):
        config = get_object_or_404(DeviceSNMPConfig, pk=pk)
        if not request.user.has_perm("netbox_snmp_sync.view_devicesnmpconfig"):
            messages.error(request, "You do not have permission to view SNMP configurations.")
            return redirect(config.get_absolute_url())
        spec = config.to_spec()
        if not spec.target:
            messages.error(request, f"{config.device}: no SNMP target (set a primary IP or target override).")
            return redirect(config.get_absolute_url())
        try:
            data = _collect_blocking(spec)
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"SNMP collection failed: {exc}")
            return redirect(config.get_absolute_url())
        diff = engine.compare_device(config.device, data)
        return render(request, self.template_name, {
            "object": config,
            "device": config.device,
            "sys_name": data.sys_name,
            "rename_device_to_sysname": config.rename_device_to_sysname,
            "rename_device_pending": _rename_device_to_sysname_pending(config, data),
            "diff": diff,
            "vlan_rows": _vlan_preview_rows(data),
            "write_vlans_enabled": bool(get_setting("write_vlans")),
            "create_vlans_enabled": bool(get_setting("create_vlans")),
            "snapshot": json.dumps(serialize_device_data(data)),
        })

    def post(self, request, pk):
        config = get_object_or_404(DeviceSNMPConfig, pk=pk)
        if not request.user.has_perm("dcim.add_interface"):
            messages.error(request, "You do not have permission to create interfaces.")
            return redirect(config.get_absolute_url())
        try:
            data = deserialize_device_data(json.loads(request.POST.get("snapshot") or "{}"))
        except Exception:  # noqa: BLE001
            messages.error(request, "Invalid snapshot — please re-run the preview.")
            return redirect(config.get_absolute_url())

        selected_ifaces = set(request.POST.getlist("iface"))
        selected_ips = set(request.POST.getlist("ip"))
        write_vlans = bool(get_setting("write_vlans"))
        create_vlans = bool(get_setting("create_vlans"))
        selected_vlan_ifaces = set(request.POST.getlist("vlan_iface")) if write_vlans else set()
        selected_ip_ifindexes = {
            ip.if_index for ip in data.ip_addresses
            if ip.address in selected_ips
        }
        selected_ip_ifaces = {
            iface.name for if_index, iface in data.interfaces.items()
            if if_index in selected_ip_ifindexes
        }
        selected_interface_names = selected_ifaces | selected_vlan_ifaces | selected_ip_ifaces
        data.interfaces = {i: f for i, f in data.interfaces.items() if f.name in selected_interface_names}
        data.ip_addresses = [ip for ip in data.ip_addresses if ip.address in selected_ips]
        rename_device_pending = _rename_device_to_sysname_pending(config, data)
        if not data.interfaces and not data.ip_addresses and not rename_device_pending:
            messages.warning(request, "Nothing selected to write.")
            return redirect(reverse("plugins:netbox_snmp_sync:devicesnmpconfig_preview", args=[pk]))

        result = engine.apply_sync(
            config.device, data, dry_run=False,
            update_existing=bool(get_setting("update_existing")),
            set_mac_address=bool(get_setting("set_mac_address")),
            write_vlans=write_vlans,
            create_vlans=create_vlans,
            rename_device_to_sysname=bool(config.rename_device_to_sysname),
        )
        message = (
            f"Interactive write: {result.interfaces_created} interfaces, {result.ips_created} IPs selected; "
            f"VLANs set {result.iface_vlans_set}, created {result.vlans_created}; "
            f"devices renamed {result.devices_updated}"
        )
        if result.warnings:
            message += "; " + "; ".join(result.warnings)
        rename_messages = _device_rename_messages(result)
        if rename_messages:
            message += "; " + "; ".join(rename_messages)
        run = SyncRun.objects.create(
            device=config.device, trigger=SyncTriggerChoices.MANUAL, mode=SyncModeChoices.APPLY,
            status=SyncStatusChoices.OK,
            interfaces_created=result.interfaces_created, interfaces_updated=result.interfaces_updated,
            interfaces_existing=result.interfaces_existing, interfaces_ignored=result.interfaces_ignored,
            ips_created=result.ips_created, ips_existing=result.ips_existing,
            vlans_created=result.vlans_created, iface_vlans_set=result.iface_vlans_set,
            message=message,
        )
        record_created_objects(run, getattr(result, "created_objects", ()))
        record_sync_changes(run, getattr(result, "changes", ()))
        config.record_sync_result(run, update_schedule=False)
        messages.success(
            request,
            f"Wrote {result.interfaces_created} interface(s), {result.ips_created} IP(s), "
            f"set VLANs on {result.iface_vlans_set} interface(s), created {result.vlans_created} VLAN(s), "
            f"renamed {result.devices_updated} device(s).",
        )
        return redirect(config.device.get_absolute_url())


@register_model_view(SyncRun, name="list", path="", detail=False)
class SyncRunListView(generic.ObjectListView):
    queryset = SyncRun.objects.all()
    table = tables.SyncRunTable
    filterset = filtersets.SyncRunFilterSet


@register_model_view(SyncRun)
class SyncRunView(generic.ObjectView):
    queryset = SyncRun.objects.all()


@register_model_view(SyncRun, name="delete")
class SyncRunDeleteView(generic.ObjectDeleteView):
    queryset = SyncRun.objects.all()


@register_model_view(SyncRun, name="bulk_delete", detail=False)
class SyncRunBulkDeleteView(generic.BulkDeleteView):
    queryset = SyncRun.objects.all()
    table = tables.SyncRunTable


class SNMPSyncSettingsView(generic.ObjectEditView):
    """Edit the plugin's global settings singleton (SNMP Sync → Settings)."""

    queryset = SNMPSyncConfig.objects.all()
    form = forms.SNMPSyncConfigForm

    def get_object(self, **kwargs):
        return SNMPSyncConfig.get()


class BulkSNMPConfigView(LoginRequiredMixin, View):
    """Create SNMP configs for many devices at once, optionally pulling each device's
    community from a custom field."""

    template_name = "netbox_snmp_sync/bulk_setup.html"

    def get(self, request):
        return render(request, self.template_name, {"form": forms.BulkSNMPConfigForm()})

    def post(self, request):
        from django.core.exceptions import ValidationError

        if not request.user.has_perm("netbox_snmp_sync.add_devicesnmpconfig"):
            messages.error(request, "You do not have permission to create SNMP configurations.")
            return redirect("plugins:netbox_snmp_sync:devicesnmpconfig_list")

        form = forms.BulkSNMPConfigForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        cd = form.cleaned_data
        cf_name = cd["community_custom_field"].strip()
        created = updated = skipped = errors = 0
        for device in cd["devices"]:
            community = cd["community"]
            if cf_name:
                val = (device.custom_field_data or {}).get(cf_name)
                if val:
                    community = str(val)
            existing = DeviceSNMPConfig.objects.filter(device=device).first()
            if existing and not cd["overwrite"]:
                skipped += 1
                continue
            obj = existing or DeviceSNMPConfig(device=device)
            obj.snmp_version = cd["snmp_version"]
            obj.community = community or ""
            obj.port = cd["port"]
            try:
                obj.full_clean()
                obj.save()
            except ValidationError:
                errors += 1
                continue
            if existing:
                updated += 1
            else:
                created += 1

        suffix = f", {errors} invalid (missing community?)" if errors else ""
        messages.success(request, f"SNMP configs — created {created}, updated {updated}, skipped {skipped}{suffix}.")
        return redirect("plugins:netbox_snmp_sync:devicesnmpconfig_list")


@register_model_view(SyncRun, name="revert")
class SyncRunRevertView(LoginRequiredMixin, View):
    """Delete the objects a sync run created (add-only → safe). Deletions run in this request,
    so they are recorded in NetBox's change log."""

    def get(self, request, pk):
        run = get_object_or_404(SyncRun, pk=pk)
        return render(request, "netbox_snmp_sync/syncrun_revert.html", {"object": run})

    def post(self, request, pk):
        run = get_object_or_404(SyncRun, pk=pk)
        if not request.user.has_perm("dcim.delete_interface"):
            messages.error(request, "You do not have permission to delete interfaces.")
            return redirect(run.get_absolute_url())
        if not run.can_revert:
            messages.warning(request, "Nothing to revert (already reverted or no created objects).")
            return redirect(run.get_absolute_url())
        deleted = run.revert()
        messages.success(request, f"Reverted run #{run.pk}: deleted {deleted} object(s).")
        return redirect(run.get_absolute_url())

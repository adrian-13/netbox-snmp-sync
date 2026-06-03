import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import View

from netbox.plugins import get_plugin_config
from netbox.views import generic
from utilities.views import register_model_view

from . import engine, filtersets, forms, tables
from .choices import SyncModeChoices, SyncStatusChoices, SyncTriggerChoices
from .dto import deserialize_device_data, serialize_device_data
from .jobs import SNMPSyncJob
from .models import DeviceSNMPConfig, SyncRun, record_created_objects
from .snmp_collector import collect_with_ping


def _collect_blocking(spec):
    """Run the async SNMP collector synchronously in a fresh thread.

    Using a dedicated thread guarantees there's no already-running event loop (which would
    break ``asyncio.run``), regardless of whether NetBox is served via WSGI or ASGI.
    """
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(collect_with_ping(spec))).result()


@register_model_view(DeviceSNMPConfig, name="list", path="", detail=False)
class DeviceSNMPConfigListView(generic.ObjectListView):
    queryset = DeviceSNMPConfig.objects.all()
    table = tables.DeviceSNMPConfigTable
    filterset = filtersets.DeviceSNMPConfigFilterSet


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


@register_model_view(DeviceSNMPConfig, name="sync")
class DeviceSNMPConfigSyncView(LoginRequiredMixin, View):
    """Enqueue an SNMP collect/compare/sync job for a device's SNMP config."""

    def get(self, request, pk):
        return self._enqueue(request, pk)

    def post(self, request, pk):
        return self._enqueue(request, pk)

    def _enqueue(self, request, pk):
        config = get_object_or_404(DeviceSNMPConfig, pk=pk)
        mode = request.GET.get("mode", "compare")
        if not request.user.has_perm("netbox_snmp_sync.view_devicesnmpconfig"):
            messages.error(request, "You do not have permission to run SNMP sync.")
            return redirect(config.get_absolute_url())
        job = SNMPSyncJob.enqueue(config_pk=config.pk, user=request.user, mode=mode)
        messages.success(request, f"Queued SNMP '{mode}' for {config.device}.")
        return redirect(job.get_absolute_url())


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
            "diff": diff,
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
        data.interfaces = {i: f for i, f in data.interfaces.items() if f.name in selected_ifaces}
        data.ip_addresses = [ip for ip in data.ip_addresses if ip.address in selected_ips]
        if not data.interfaces and not data.ip_addresses:
            messages.warning(request, "Nothing selected to write.")
            return redirect(reverse("plugins:netbox_snmp_sync:devicesnmpconfig_preview", args=[pk]))

        result = engine.apply_sync(
            config.device, data, dry_run=False,
            update_existing=bool(get_plugin_config("netbox_snmp_sync", "update_existing")),
            set_mac_address=bool(get_plugin_config("netbox_snmp_sync", "set_mac_address")),
            write_vlans=bool(get_plugin_config("netbox_snmp_sync", "write_vlans")),
            create_vlans=bool(get_plugin_config("netbox_snmp_sync", "create_vlans")),
        )
        run = SyncRun.objects.create(
            device=config.device, trigger=SyncTriggerChoices.MANUAL, mode=SyncModeChoices.APPLY,
            status=SyncStatusChoices.OK,
            interfaces_created=result.interfaces_created, interfaces_updated=result.interfaces_updated,
            interfaces_existing=result.interfaces_existing, interfaces_ignored=result.interfaces_ignored,
            ips_created=result.ips_created, ips_existing=result.ips_existing,
            message=f"Interactive write: {result.interfaces_created} interfaces, {result.ips_created} IPs selected.",
        )
        record_created_objects(run, result.created_objects)
        messages.success(
            request,
            f"Wrote {result.interfaces_created} interface(s) and {result.ips_created} IP(s).",
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

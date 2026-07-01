from netbox.plugins import PluginTemplateExtension

from .models import SyncRun


class DeviceSNMPPanel(PluginTemplateExtension):
    """Show the device's SNMP Sync configuration (or an Add button) on the device page."""

    models = ["dcim.device"]

    def right_page(self):
        request = self.context.get("request")
        if request is None:
            return ""

        user = request.user
        can_view_config = user.has_perm("netbox_snmp_sync.view_devicesnmpconfig")
        can_add_config = user.has_perm("netbox_snmp_sync.add_devicesnmpconfig")
        can_change_config = user.has_perm("netbox_snmp_sync.change_devicesnmpconfig")
        can_view_run = user.has_perm("netbox_snmp_sync.view_syncrun")

        device = self.context["object"]
        snmp_config = getattr(device, "snmp_config", None)

        # Hide the whole plugin panel when the user cannot see or create SNMP config.
        if (snmp_config and not can_view_config) or (not snmp_config and not can_add_config):
            return ""

        last_sync = None
        if can_view_run:
            last_sync = SyncRun.objects.filter(device=device).order_by("-created").first()

        return self.render(
            "netbox_snmp_sync/device_snmp_panel.html",
            extra_context={
                "snmp_config": snmp_config,
                "last_sync": last_sync,
                "can_add_config": can_add_config,
                "can_change_config": can_change_config,
                "can_view_config": can_view_config,
            },
        )


template_extensions = [DeviceSNMPPanel]

from netbox.plugins import PluginTemplateExtension

from .models import SyncRun


class DeviceSNMPPanel(PluginTemplateExtension):
    """Show the device's SNMP Sync configuration (or an Add button) on the device page."""

    models = ["dcim.device"]

    def right_page(self):
        device = self.context["object"]
        last_sync = SyncRun.objects.filter(device=device).order_by("-created").first()
        return self.render(
            "netbox_snmp_sync/device_snmp_panel.html",
            extra_context={
                "snmp_config": getattr(device, "snmp_config", None),
                "last_sync": last_sync,
            },
        )


template_extensions = [DeviceSNMPPanel]

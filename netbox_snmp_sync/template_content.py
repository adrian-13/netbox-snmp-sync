from netbox.plugins import PluginTemplateExtension


class DeviceSNMPPanel(PluginTemplateExtension):
    """Show the device's SNMP Sync configuration (or an Add button) on the device page."""

    models = ["dcim.device"]

    def right_page(self):
        device = self.context["object"]
        return self.render(
            "netbox_snmp_sync/device_snmp_panel.html",
            extra_context={"snmp_config": getattr(device, "snmp_config", None)},
        )


template_extensions = [DeviceSNMPPanel]

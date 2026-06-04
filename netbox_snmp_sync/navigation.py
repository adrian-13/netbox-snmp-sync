from netbox.choices import ButtonColorChoices
from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

menu = PluginMenu(
    label="SNMP Sync",
    icon_class="mdi mdi-lan-connect",
    groups=(
        (
            "SNMP Sync",
            (
                PluginMenuItem(
                    link="plugins:netbox_snmp_sync:devicesnmpconfig_list",
                    link_text="Device SNMP Configs",
                    buttons=(
                        PluginMenuButton(
                            link="plugins:netbox_snmp_sync:devicesnmpconfig_add",
                            title="Add",
                            icon_class="mdi mdi-plus-thick",
                            color=ButtonColorChoices.GREEN,
                        ),
                    ),
                ),
                PluginMenuItem(
                    link="plugins:netbox_snmp_sync:syncrun_list",
                    link_text="Sync Runs",
                ),
                PluginMenuItem(
                    link="plugins:netbox_snmp_sync:settings",
                    link_text="Settings",
                ),
                # 'Bulk setup' is intentionally not in the menu (reachable by direct URL:
                # /plugins/snmp-sync/bulk-setup/). Use Device SNMP Configs → Import for bulk create.
            ),
        ),
    ),
)

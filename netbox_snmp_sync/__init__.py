"""NetBox SNMP Sync — read interface/IP/VLAN data from network devices over SNMP
and reconcile it into NetBox (add-only by default), all from within NetBox itself.

This is the in-NetBox plugin successor to the standalone ``netbox-snmp-sync`` tool:
the SNMP collection and mapping logic is reused, but device data is written directly
through the Django ORM and the workflow (collect → diff → write, plus scheduling and
run history) lives natively in the NetBox UI and its background-job framework.
"""
from netbox.plugins import PluginConfig

__version__ = "0.3.0"


class NetBoxSNMPSyncConfig(PluginConfig):
    name = "netbox_snmp_sync"
    verbose_name = "NetBox SNMP Sync"
    description = "Collect interfaces, IPs and VLANs from devices over SNMP and sync them into NetBox."
    version = __version__
    author = "Adrián Javorček"
    author_email = "adrian.javorcek@gmail.com"
    base_url = "snmp-sync"
    min_version = "4.6.0"

    # Defaults applied when a device has no per-device SNMP configuration override.
    # Mirrors the ``defaults`` block of the standalone tool's config.yaml.
    default_settings = {
        # SNMP transport / credentials
        "snmp_version": "2c",            # "1" | "2c" | "3"
        "snmp_port": 161,
        "snmp_community": "public",      # v1/v2c
        "snmp_timeout": 2.0,
        "snmp_retries": 1,
        # SNMPv3 defaults
        "snmp_auth_protocol": "none",    # none | md5 | sha | sha224 | sha256 | sha384 | sha512
        "snmp_priv_protocol": "none",    # none | des | 3des | aes128 | aes192 | aes256
        # interface typing / behaviour (see standalone DeviceConfig)
        "default_ethernet_type": "1000base-t",
        "set_mac_address": True,
        "update_existing": False,        # also overwrite changed fields on existing interfaces
        "skip_loopback_ips": True,
        # VLAN membership (off by default — only works if VLANs exist, writes to existing ifaces)
        "write_vlans": False,
        "create_vlans": False,           # auto-create missing VLANs in the device's site
        # scheduler: hours between automatic syncs; 0 disables the periodic job
        "sync_interval_hours": 0,
        # scheduler: restrict syncs to these hours of the day (e.g. "3" or "3,15"); blank = any
        "sync_at_hours": "",
        # background job safety limit; 0 disables the wrapper timeout
        "sync_job_timeout_seconds": 300,
        # history retention (daily prune job): keep at most N runs and/or runs newer than D days
        "history_keep_days": 90,
        "history_keep_count": 1000,
    }

    def ready(self):
        super().ready()
        # Import jobs so the @system_job-decorated scheduler is registered with the RQ worker.
        from . import jobs  # noqa: F401


config = NetBoxSNMPSyncConfig

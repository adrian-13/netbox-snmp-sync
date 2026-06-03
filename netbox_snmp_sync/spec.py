"""Lightweight input spec consumed by the SNMP collector.

This is the subset of the standalone tool's ``DeviceConfig`` that ``snmp_collector``
actually reads. Keeping it as a plain dataclass (no Django imports) lets the collector
stay framework-agnostic and unit-testable; the plugin's job layer builds one of these
from the ``DeviceSNMPConfig`` ORM model merged with the plugin's default settings.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeviceConfig:
    target: str                              # host/IP to poll
    snmp_version: str = "2c"                 # "1" | "2c" | "3"
    snmp_community: str | None = None        # v1/v2c
    snmp_port: int = 161
    timeout: float = 2.0
    retries: int = 1
    # SNMPv3
    snmp_user: str | None = None
    snmp_auth_protocol: str = "none"
    snmp_auth_key: str | None = None
    snmp_priv_protocol: str = "none"
    snmp_priv_key: str | None = None
    # typing / behaviour
    vendor: str | None = None                # manual override; else detected from sysObjectID
    default_ethernet_type: str = "1000base-t"
    skip_loopback_ips: bool = True

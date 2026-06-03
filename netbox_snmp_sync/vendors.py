"""Vendor-specific SNMP mapping (interface NAME -> NetBox type, per vendor).

Vendors name ports consistently enough that the name is a more reliable medium hint than the
(possibly down / auto-negotiated) link speed — e.g. a MikroTik ``sfp-sfpplus1`` is a 10G SFP+
even when it currently reports 0/1000 Mb.

The active rule set is editable at runtime (UI / config); ``DEFAULT_VENDOR_IFACE_RULES`` is the
built-in seed used when the config has no ``vendor_mappings`` section. Rules are
(regex on the interface name, NetBox type slug); first match wins.
"""
from __future__ import annotations

import re

DEFAULT_VENDOR_IFACE_RULES: dict[str, list[tuple[str, str]]] = {
    "mikrotik": [
        (r"^sfp-?sfpplus", "10gbase-x-sfpp"),
        (r"^sfp28", "25gbase-x-sfp28"),
        (r"^qsfp28", "100gbase-x-qsfp28"),
        (r"^qsfp", "40gbase-x-qsfpp"),
        (r"^sfp", "1000base-x-sfp"),
        (r"^ether", "1000base-t"),
        (r"^(wlan|wifi)", "ieee802.11ac"),
    ],
    "cisco": [
        (r"^(hu|hundredgig)", "100gbase-x-qsfp28"),
        (r"^(fo|fortygig)", "40gbase-x-qsfpp"),
        (r"^(twe|twentyfivegig)", "25gbase-x-sfp28"),
        (r"^(te|tengig)", "10gbase-x-sfpp"),
        (r"^(gi|gigabitethernet)", "1000base-t"),
        (r"^(fa|fastethernet)", "100base-tx"),
    ],
    "huawei": [
        (r"^100ge", "100gbase-x-qsfp28"),
        (r"^40ge", "40gbase-x-qsfpp"),
        (r"^25ge", "25gbase-x-sfp28"),
        (r"^(xge|10ge|xgigabitethernet)", "10gbase-x-sfpp"),
        (r"^(ge|gigabitethernet)", "1000base-t"),
        (r"^eth-?trunk", "lag"),
    ],
}


def _clone(rules: dict[str, list[tuple[str, str]]]) -> dict[str, list[tuple[str, str]]]:
    return {vendor: [(p, t) for p, t in lst] for vendor, lst in rules.items()}


def _compile(rules: dict[str, list[tuple[str, str]]]) -> dict[str, list]:
    out: dict[str, list] = {}
    for vendor, lst in rules.items():
        compiled = []
        for pat, typ in lst:
            try:
                compiled.append((re.compile(pat, re.IGNORECASE), typ))
            except re.error:
                continue  # skip invalid regex rather than break everything
        out[vendor.lower()] = compiled
    return out


_active_rules: dict[str, list[tuple[str, str]]] = _clone(DEFAULT_VENDOR_IFACE_RULES)
_active_compiled: dict[str, list] = _compile(_active_rules)


def set_active_rules(rules: dict[str, list[tuple[str, str]]]) -> None:
    """Replace the active vendor rule set (used by the collector immediately)."""
    global _active_rules, _active_compiled
    _active_rules = _clone(rules)
    _active_compiled = _compile(_active_rules)


def vendor_rules() -> dict[str, list[tuple[str, str]]]:
    """Current active rules (for the UI / display)."""
    return _active_rules


def vendor_iface_type(vendor: str | None, name: str | None) -> str | None:
    """NetBox type for an interface name under a vendor's naming scheme, or None."""
    if not vendor or not name:
        return None
    rules = _active_compiled.get(vendor.lower())
    if not rules:
        return None
    for rx, typ in rules:
        if rx.search(name):
            return typ
    return None

from __future__ import annotations

# IANAifType numbers that represent an Ethernet-like port. These get a type
# derived from the link speed (see _SPEED_MAP), falling back to the configured
# default when speed is unknown. SNMP cannot tell copper from fibre, so the
# result is a best-effort guess the user refines in NetBox.
_ETHERNET = {6, 7, 62, 69, 117}

# Link speed (kbps, from ifHighSpeed * 1000) -> best-guess NetBox type slug.
# >=10G assumes SFP/QSFP optics (most common in switching gear); copper variants
# (e.g. 10gbase-t) are refined by hand if needed. This is the built-in seed; the
# active map is runtime-replaceable (UI / config) via set_active_speed_map().
DEFAULT_SPEED_MAP = {
    10_000: "100base-tx",        # 10M
    100_000: "100base-tx",       # 100M
    1_000_000: "1000base-t",     # 1G
    2_500_000: "2.5gbase-t",     # 2.5G
    5_000_000: "5gbase-t",       # 5G
    10_000_000: "10gbase-x-sfpp",    # 10G
    25_000_000: "25gbase-x-sfp28",   # 25G
    40_000_000: "40gbase-x-qsfpp",   # 40G
    50_000_000: "50gbase-x-sfp56",   # 50G
    100_000_000: "100gbase-x-qsfp28",  # 100G
    200_000_000: "200gbase-x-qsfp56",  # 200G
    400_000_000: "400gbase-x-qsfp112",  # 400G
}

# Active speed map (kbps -> slug); replaced at runtime by set_active_speed_map().
_active_speed_map: dict[int, str] = dict(DEFAULT_SPEED_MAP)


def set_active_speed_map(mapping: dict[int, str]) -> None:
    """Replace the active speed->type map (kbps keyed). Used by the collector immediately."""
    global _active_speed_map
    _active_speed_map = {int(k): str(v) for k, v in mapping.items()}


def speed_map() -> dict[int, str]:
    """Current active speed->type map (kbps keyed), for the UI / display."""
    return _active_speed_map

# IANAifType -> fixed NetBox interface type slug (speed-independent).
_FIXED = {
    24: "virtual",        # softwareLoopback
    53: "virtual",        # propVirtual
    71: "other-wireless",  # ieee80211
    131: "virtual",       # tunnel
    135: "virtual",       # l2vlan
    136: "virtual",       # l3ipvlan
    150: "virtual",       # mplsTunnel
    161: "lag",           # ieee8023adLag
    209: "bridge",        # bridge
}

# Human-readable names for the if_types we commonly see, for logs.
IF_TYPE_NAMES = {
    1: "other",
    6: "ethernetCsmacd",
    24: "softwareLoopback",
    53: "propVirtual",
    62: "fastEther",
    71: "ieee80211",
    117: "gigabitEthernet",
    131: "tunnel",
    135: "l2vlan",
    136: "l3ipvlan",
    161: "ieee8023adLag",
    209: "bridge",
}

# All valid NetBox interface type slugs (used to validate config). Sourced from
# the NetBox interface import "type" choices.
VALID_INTERFACE_TYPES = frozenset({
    "virtual", "bridge", "lag",
    "100base-fx", "100base-lfx", "100base-tx", "100base-t1",
    "1000base-t", "1000base-lx", "1000base-tx",
    "2.5gbase-t", "5gbase-t", "10gbase-t", "10gbase-cx4",
    "100base-x-sfp", "1000base-x-gbic", "1000base-x-sfp",
    "10gbase-x-sfpp", "10gbase-x-xfp", "10gbase-x-xenpak", "10gbase-x-x2",
    "25gbase-x-sfp28", "50gbase-x-sfp56", "40gbase-x-qsfpp",
    "100gbase-x-cfp", "100gbase-x-cfp2", "200gbase-x-cfp2", "400gbase-x-cfp2",
    "100gbase-x-cfp4", "100gbase-x-cxp", "100gbase-x-cpak", "100gbase-x-dsfp",
    "100gbase-x-sfpdd", "100gbase-x-qsfp28", "100gbase-x-qsfpdd",
    "200gbase-x-qsfp56", "200gbase-x-qsfpdd",
    "400gbase-x-qsfp112", "400gbase-x-qsfpdd", "400gbase-x-osfp",
    "400gbase-x-osfp-rhs", "400gbase-x-cdfp", "400gbase-x-cfp8",
    "800gbase-x-qsfpdd", "800gbase-x-osfp",
    "1000base-kx", "2.5gbase-kx", "5gbase-kr", "10gbase-kr", "10gbase-kx4",
    "25gbase-kr", "40gbase-kr4", "50gbase-kr", "100gbase-kp4",
    "100gbase-kr2", "100gbase-kr4",
    "ieee802.11a", "ieee802.11g", "ieee802.11n", "ieee802.11ac",
    "ieee802.11ad", "ieee802.11ax", "ieee802.11ay", "ieee802.11be",
    "ieee802.15.1", "ieee802.15.4", "other-wireless",
    "gsm", "cdma", "lte", "4g", "5g",
    "sonet-oc3", "sonet-oc12", "sonet-oc48", "sonet-oc192", "sonet-oc768",
    "sonet-oc1920", "sonet-oc3840",
    "1gfc-sfp", "2gfc-sfp", "4gfc-sfp", "8gfc-sfpp", "16gfc-sfpp",
    "32gfc-sfp28", "32gfc-sfpp", "64gfc-qsfpp", "64gfc-sfpdd", "64gfc-sfpp",
    "128gfc-qsfp28",
    "infiniband-sdr", "infiniband-ddr", "infiniband-qdr", "infiniband-fdr10",
    "infiniband-fdr", "infiniband-edr", "infiniband-hdr", "infiniband-ndr",
    "infiniband-xdr",
    "t1", "e1", "t3", "e3", "xdsl", "docsis",
    "bpon", "epon", "10g-epon", "gpon", "xg-pon", "xgs-pon", "ng-pon2",
    "25g-pon", "50g-pon",
    "cisco-stackwise", "cisco-stackwise-plus", "cisco-flexstack",
    "cisco-flexstack-plus", "cisco-stackwise-80", "cisco-stackwise-160",
    "cisco-stackwise-320", "cisco-stackwise-480", "cisco-stackwise-1t",
    "juniper-vcp", "extreme-summitstack", "extreme-summitstack-128",
    "extreme-summitstack-256", "extreme-summitstack-512",
    "other",
})


def netbox_interface_type(if_type: int, speed_kbps: int | None, default_ethernet_type: str,
                          name: str | None = None, vendor: str | None = None) -> str:
    # Vendor-specific name rule wins (most reliable physical-medium hint).
    from .vendors import vendor_iface_type
    vt = vendor_iface_type(vendor, name)
    if vt:
        return vt
    if if_type in _ETHERNET:
        if speed_kbps and speed_kbps in _active_speed_map:
            return _active_speed_map[speed_kbps]
        return default_ethernet_type
    return _FIXED.get(if_type, "other")

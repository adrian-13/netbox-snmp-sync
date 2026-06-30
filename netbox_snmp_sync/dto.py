from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InterfaceData:
    if_index: int
    name: str
    if_type: int  # IANAifType number from IF-MIB
    mtu: int | None = None
    mac: str | None = None  # "AA:BB:CC:DD:EE:FF"
    enabled: bool = True  # ifAdminStatus == up
    description: str = ""  # ifAlias
    speed_kbps: int | None = None  # derived from ifHighSpeed (Mbps)
    duplex: str | None = None  # "half" | "full" from dot3StatsDuplexStatus
    parent_name: str | None = None  # name of parent interface (sub-interface like "bridge.650")
    nb_type: str = "other"  # NetBox interface type (vendor/name/speed-aware), computed at collect time
    access_vlan: int | None = None  # untagged/native VLAN (dot1qPvid); None if unknown
    tagged_vlans: list[int] = field(default_factory=list)  # trunk VLANs carried on this port


@dataclass
class IPAddressData:
    address: str  # "192.168.1.1/24"
    if_index: int  # interface this IP is bound to (ifIndex)


@dataclass
class VlanData:
    vid: int  # 802.1Q VLAN ID (dot1qVlanIndex)
    name: str  # dot1qVlanStaticName


@dataclass
class LldpNeighbor:
    """One LLDP neighbor seen on a local port (from LLDP-MIB lldpRemTable)."""
    local_if_index: int       # ifIndex of OUR interface that sees the neighbor
    local_port: str           # name of OUR interface (e.g. "ether1")
    remote_system: str        # neighbor's sysName (lldpRemSysName)
    remote_port: str          # human-readable port name on the neighbor (lldpRemPortDesc / -Id)
    remote_chassis_id: str | None = None  # MAC of remote chassis, if subtype=macAddress


@dataclass
class DeviceData:
    target: str  # host/IP that was polled
    sys_name: str | None = None
    sys_descr: str | None = None
    vendor: str | None = None    # derived from sysObjectID enterprise number
    vlan_subinterface_inference: str = "auto"  # auto | enabled | disabled
    model: str | None = None     # ENTITY-MIB chassis model name (best-effort)
    serial: str | None = None    # ENTITY-MIB chassis serial number (best-effort)
    interfaces: dict[int, InterfaceData] = field(default_factory=dict)
    ip_addresses: list[IPAddressData] = field(default_factory=list)
    vlans: list[VlanData] = field(default_factory=list)
    lldp_neighbors: list[LldpNeighbor] = field(default_factory=list)


def serialize_device_data(data: "DeviceData") -> dict:
    """JSON-safe snapshot of a DeviceData — used to carry a poll result through the
    interactive preview form so 'write selected' needn't re-poll the device."""
    from dataclasses import asdict

    return asdict(data)


def deserialize_device_data(d: dict) -> "DeviceData":
    """Rebuild a DeviceData from ``serialize_device_data`` output. LLDP is dropped (unused)."""
    dev = DeviceData(
        target=d.get("target", ""),
        sys_name=d.get("sys_name"),
        sys_descr=d.get("sys_descr"),
        vendor=d.get("vendor"),
        vlan_subinterface_inference=d.get("vlan_subinterface_inference") or "auto",
        model=d.get("model"),
        serial=d.get("serial"),
    )
    for f in (d.get("interfaces") or {}).values():
        dev.interfaces[int(f["if_index"])] = InterfaceData(**f)
    for ip in d.get("ip_addresses") or []:
        dev.ip_addresses.append(IPAddressData(**ip))
    for v in d.get("vlans") or []:
        dev.vlans.append(VlanData(**v))
    return dev

from __future__ import annotations

import pysnmp.hlapi.v3arch.asyncio as h
from pysnmp.proto import rfc1905

from .spec import DeviceConfig
from .if_types import netbox_interface_type
from .dto import DeviceData, InterfaceData, IPAddressData, LldpNeighbor, VlanData

# --- OIDs (numeric, no MIB lookup needed) -------------------------------------
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"

# sysObjectID enterprise number (1.3.6.1.4.1.<N>...) -> vendor name.
_ENTERPRISE_VENDORS = {
    "9": "Cisco", "14988": "MikroTik", "2011": "Huawei", "2636": "Juniper",
    "11": "HPE", "25506": "H3C", "6527": "Nokia", "1916": "Extreme", "30065": "Arista",
    "890": "Zyxel", "4526": "Netgear", "2356": "DrayTek", "12356": "Fortinet",
    "674": "Dell", "8072": "Linux (net-snmp)", "2435": "Brother", "318": "APC",
}


def _vendor_from_sysoid(oid_str: str | None) -> str | None:
    if not oid_str:
        return None
    parts = oid_str.lstrip(".").split(".")
    if parts[:6] == ["1", "3", "6", "1", "4", "1"] and len(parts) > 6:
        return _ENTERPRISE_VENDORS.get(parts[6])
    return None

# IF-MIB ifTable
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
OID_IF_MTU = "1.3.6.1.2.1.2.2.1.4"
OID_IF_PHYS_ADDRESS = "1.3.6.1.2.1.2.2.1.6"
OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
# IF-MIB ifXTable
OID_IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
OID_IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"
OID_IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

# EtherLike-MIB dot3StatsDuplexStatus (indexed by ifIndex)
OID_DOT3_DUPLEX = "1.3.6.1.2.1.10.7.2.1.19"

# IP-MIB ipAddrTable (IPv4)
OID_IP_AD_ENT_IFINDEX = "1.3.6.1.2.1.4.20.1.2"
OID_IP_AD_ENT_NETMASK = "1.3.6.1.2.1.4.20.1.3"

# Q-BRIDGE-MIB dot1qVlanStaticName (indexed by VLAN ID)
OID_DOT1Q_VLAN_STATIC_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"
# Q-BRIDGE-MIB PortList bitmaps. Current table is indexed by TimeFilter + VLAN ID;
# static table is indexed by VLAN ID. PortList bits map to dot1dBasePort numbers.
OID_DOT1Q_VLAN_CURRENT_EGRESS_PORTS = "1.3.6.1.2.1.17.7.1.4.2.1.4"
OID_DOT1Q_VLAN_CURRENT_UNTAGGED_PORTS = "1.3.6.1.2.1.17.7.1.4.2.1.5"
OID_DOT1Q_VLAN_STATIC_EGRESS_PORTS = "1.3.6.1.2.1.17.7.1.4.3.1.2"
OID_DOT1Q_VLAN_STATIC_UNTAGGED_PORTS = "1.3.6.1.2.1.17.7.1.4.3.1.4"
# Q-BRIDGE-MIB dot1qPvid — untagged/native VLAN per bridge port (indexed by dot1dBasePort).
OID_DOT1Q_PVID = "1.3.6.1.2.1.17.7.1.4.5.1.1"
# BRIDGE-MIB dot1dBasePortIfIndex — maps bridge-port number -> ifIndex.
OID_DOT1D_BASE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"

# CISCO-VTP-MIB vtpVlanName, indexed by managementDomainIndex + VLAN ID. Some Catalyst
# platforms expose VLAN names here even when the standard Q-BRIDGE static table is sparse.
OID_CISCO_VTP_VLAN_NAME = "1.3.6.1.4.1.9.9.46.1.3.1.1.4"

# CISCO-VLAN-MEMBERSHIP-MIB vmVlan (indexed by ifIndex): access VLAN for
# non-trunk bridge ports.
OID_CISCO_VM_VLAN = "1.3.6.1.4.1.9.9.68.1.2.2.1.2"

# CISCO-VTP-MIB vlanTrunkPortTable (indexed by ifIndex). Enabled VLAN bitmaps
# are split into 0-1023, 1024-2047, 2048-3071, and 3072-4095 ranges.
OID_CISCO_TRUNK_VLANS_ENABLED = "1.3.6.1.4.1.9.9.46.1.6.1.1.4"
OID_CISCO_TRUNK_NATIVE_VLAN = "1.3.6.1.4.1.9.9.46.1.6.1.1.5"
OID_CISCO_TRUNK_DYNAMIC_STATUS = "1.3.6.1.4.1.9.9.46.1.6.1.1.14"
OID_CISCO_TRUNK_VLANS_ENABLED_2K = "1.3.6.1.4.1.9.9.46.1.6.1.1.17"
OID_CISCO_TRUNK_VLANS_ENABLED_3K = "1.3.6.1.4.1.9.9.46.1.6.1.1.18"
OID_CISCO_TRUNK_VLANS_ENABLED_4K = "1.3.6.1.4.1.9.9.46.1.6.1.1.19"

# ENTITY-MIB physical components — used to detect device model + chassis serial.
# entPhysicalClass: 3 = chassis (the row we care about). modelName/serial then come from
# the same physical-index row. Most enterprise gear (MikroTik, Cisco, Huawei, Juniper, …)
# implements this; missing data is fine, we just won't pre-fill anything.
OID_ENT_PHYSICAL_CLASS = "1.3.6.1.2.1.47.1.1.1.1.5"
OID_ENT_PHYSICAL_MODEL = "1.3.6.1.2.1.47.1.1.1.1.13"
OID_ENT_PHYSICAL_SERIAL = "1.3.6.1.2.1.47.1.1.1.1.11"

# LLDP-MIB (IEEE 802.1AB-2005). Local port table (indexed by lldpLocPortNum) maps to a
# human-readable port description we use to find the matching ifIndex on our side.
OID_LLDP_LOC_PORT_DESC = "1.0.8802.1.1.2.1.3.7.1.4"
# Remote (neighbor) table — indexed by ``TimeMark.LocalPortNum.RemIndex``.
OID_LLDP_REM_CHASSIS_ID_SUBTYPE = "1.0.8802.1.1.2.1.4.1.1.4"
OID_LLDP_REM_CHASSIS_ID = "1.0.8802.1.1.2.1.4.1.1.5"
OID_LLDP_REM_PORT_ID_SUBTYPE = "1.0.8802.1.1.2.1.4.1.1.6"
OID_LLDP_REM_PORT_ID = "1.0.8802.1.1.2.1.4.1.1.7"
OID_LLDP_REM_PORT_DESC = "1.0.8802.1.1.2.1.4.1.1.8"
OID_LLDP_REM_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.9"

# dot3StatsDuplexStatus: 1=unknown, 2=halfDuplex, 3=fullDuplex
_DUPLEX_MAP = {2: "half", 3: "full"}

_AUTH_PROTO = {
    "none": h.usmNoAuthProtocol,
    "md5": h.usmHMACMD5AuthProtocol,
    "sha": h.usmHMACSHAAuthProtocol,
    "sha224": h.usmHMAC128SHA224AuthProtocol,
    "sha256": h.usmHMAC192SHA256AuthProtocol,
    "sha384": h.usmHMAC256SHA384AuthProtocol,
    "sha512": h.usmHMAC384SHA512AuthProtocol,
}
_PRIV_PROTO = {
    "none": h.usmNoPrivProtocol,
    "des": h.usmDESPrivProtocol,
    "aes": h.usmAesCfb128Protocol,
    "aes128": h.usmAesCfb128Protocol,
    "aes192": h.usmAesCfb192Protocol,
    "aes256": h.usmAesCfb256Protocol,
}

_MISSING_VALUE = (rfc1905.NoSuchObject, rfc1905.NoSuchInstance, rfc1905.EndOfMibView)


class SnmpError(Exception):
    pass


def _build_auth(dev: DeviceConfig):
    version = str(dev.snmp_version).lower()
    if version in ("2c", "2", "v2c"):
        if not dev.snmp_community:
            raise SnmpError(f"{dev.target}: snmp_community is required for SNMPv2c")
        return h.CommunityData(dev.snmp_community, mpModel=1)
    if version in ("1", "v1"):
        if not dev.snmp_community:
            raise SnmpError(f"{dev.target}: snmp_community is required for SNMPv1")
        return h.CommunityData(dev.snmp_community, mpModel=0)
    if version in ("3", "v3"):
        if not dev.snmp_user:
            raise SnmpError(f"{dev.target}: snmp_user is required for SNMPv3")
        auth_proto = _AUTH_PROTO.get(str(dev.snmp_auth_protocol).lower())
        priv_proto = _PRIV_PROTO.get(str(dev.snmp_priv_protocol).lower())
        if auth_proto is None:
            raise SnmpError(f"{dev.target}: unknown snmp_auth_protocol {dev.snmp_auth_protocol!r}")
        if priv_proto is None:
            raise SnmpError(f"{dev.target}: unknown snmp_priv_protocol {dev.snmp_priv_protocol!r}")
        return h.UsmUserData(
            dev.snmp_user,
            authKey=dev.snmp_auth_key,
            privKey=dev.snmp_priv_key,
            authProtocol=auth_proto,
            privProtocol=priv_proto,
        )
    raise SnmpError(f"{dev.target}: unsupported snmp_version {dev.snmp_version!r}")


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_str(value) -> str:
    try:
        return value.prettyPrint()
    except AttributeError:
        return str(value)


def _format_mac(value) -> str | None:
    try:
        octets = value.asOctets()
    except AttributeError:
        return None
    if len(octets) != 6:
        return None
    mac = ":".join(f"{b:02X}" for b in octets)
    if mac == "00:00:00:00:00:00":
        return None
    return mac


def _oid_suffix_vlan_id(suffix: str) -> int | None:
    """Return the VLAN ID from a table suffix.

    Q-BRIDGE static rows are indexed by VLAN ID (``10``), current rows by
    TimeFilter + VLAN ID (``0.10``), and CISCO-VTP rows by domain + VLAN ID
    (``1.10``). In all useful cases the last suffix component is the VID.
    """
    try:
        vid = int(str(suffix).split(".")[-1])
    except (TypeError, ValueError):
        return None
    return vid if 1 <= vid <= 4094 else None


def _as_octets(value) -> bytes:
    if value is None:
        return b""
    try:
        return value.asOctets()
    except AttributeError:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
    return b""


def _port_list_bridge_ports(value) -> set[int]:
    """Decode an SNMP PortList OCTET STRING into dot1dBasePort numbers."""
    octets = _as_octets(value)
    ports: set[int] = set()
    for byte_index, byte in enumerate(octets):
        for bit_index in range(8):
            if byte & (1 << (7 - bit_index)):
                ports.add(byte_index * 8 + bit_index + 1)
    return ports


def _vlan_list(value, *, base: int = 0) -> set[int]:
    """Decode a Cisco VTP VLAN bitmap into VLAN IDs."""
    octets = _as_octets(value)
    vlans: set[int] = set()
    for byte_index, byte in enumerate(octets):
        for bit_index in range(8):
            if byte & (1 << (7 - bit_index)):
                vid = base + byte_index * 8 + bit_index
                if 1 <= vid <= 4094:
                    vlans.add(vid)
    return vlans


def _mask_to_prefix(mask_dotted: str) -> int | None:
    try:
        parts = [int(p) for p in mask_dotted.split(".")]
    except ValueError:
        return None
    if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
        return None
    bits = "".join(f"{p:08b}" for p in parts)
    return bits.count("1")


def _is_skippable_ip(ip: str) -> bool:
    return (
        ip.startswith("127.")
        or ip.startswith("169.254.")
        or ip == "0.0.0.0"
    )


async def _walk(engine, auth, target, base_oid: str) -> dict[str, object]:
    """Walk a table column; return {oid_suffix: value} for the subtree only."""
    result: dict[str, object] = {}
    base_len = len(base_oid.split("."))
    async for err_ind, err_stat, err_idx, var_binds in h.walk_cmd(
        engine,
        auth,
        target,
        h.ContextData(),
        h.ObjectType(h.ObjectIdentity(base_oid)),
        lexicographicMode=False,
        lookupMib=False,
    ):
        if err_ind:
            raise SnmpError(f"{base_oid}: {err_ind}")
        if err_stat:
            raise SnmpError(f"{base_oid}: {err_stat.prettyPrint()} at index {err_idx}")
        for name, value in var_binds:
            if isinstance(value, _MISSING_VALUE):
                continue
            suffix = ".".join(name.prettyPrint().split(".")[base_len:])
            result[suffix] = value
    return result


async def _get(engine, auth, target, oid: str):
    err_ind, err_stat, err_idx, var_binds = await h.get_cmd(
        engine,
        auth,
        target,
        h.ContextData(),
        h.ObjectType(h.ObjectIdentity(oid)),
        lookupMib=False,
    )
    if err_ind:
        raise SnmpError(f"{oid}: {err_ind}")
    if err_stat:
        raise SnmpError(f"{oid}: {err_stat.prettyPrint()} at index {err_idx}")
    for _name, value in var_binds:
        if isinstance(value, _MISSING_VALUE):
            return None
        return value
    return None


async def quick_snmp_ping(dev: DeviceConfig, timeout: float = 1.5) -> tuple[bool, str | None]:
    """Fast reachability probe — one ``sysName`` GET with a tight timeout, no retries.

    Returns ``(alive, error_message_or_None)``. Never raises — every failure is captured
    and reported via the second tuple element so callers can show a meaningful badge
    without try/except boilerplate. Used to pre-check devices before a full collect so
    we don't burn the 4-6s default timeout on each dead node.
    """
    from dataclasses import replace as _replace
    try:
        probe = _replace(dev, timeout=timeout, retries=0)
        auth = _build_auth(probe)
    except Exception as exc:  # noqa: BLE001 — bad community/v3 creds, missing fields, etc.
        return False, f"konfigurácia: {exc}"
    try:
        engine = h.SnmpEngine()
        target = await h.UdpTransportTarget.create(
            (probe.target, probe.snmp_port),
            timeout=probe.timeout, retries=probe.retries,
        )
        val = await _get(engine, auth, target, OID_SYS_NAME)
    except SnmpError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 — DNS failure, OS errors, etc.
        return False, str(exc)
    if val is None:
        return False, "žiadna odpoveď"
    return True, None


async def quick_snmp_sys_name(dev: DeviceConfig, timeout: float = 1.5) -> tuple[str | None, str | None]:
    """Fast test probe: one sysName GET with a tight timeout and no table walks.

    Returns (sys_name_or_None, error_message_or_None) and never raises.
    """
    from dataclasses import replace as _replace
    try:
        probe = _replace(dev, timeout=timeout, retries=0)
        auth = _build_auth(probe)
    except Exception as exc:  # noqa: BLE001
        return None, f"configuration: {exc}"
    try:
        engine = h.SnmpEngine()
        target = await h.UdpTransportTarget.create(
            (probe.target, probe.snmp_port),
            timeout=probe.timeout,
            retries=probe.retries,
        )
        val = await _get(engine, auth, target, OID_SYS_NAME)
    except SnmpError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    if val is None:
        return None, "no response"
    return _to_str(val), None


async def collect(dev: DeviceConfig) -> DeviceData:
    """Poll one device over SNMP and return a normalized DeviceData."""
    auth = _build_auth(dev)
    engine = h.SnmpEngine()
    target = await h.UdpTransportTarget.create(
        (dev.target, dev.snmp_port),
        timeout=dev.timeout,
        retries=dev.retries,
    )

    sys_name_val = await _get(engine, auth, target, OID_SYS_NAME)
    sys_descr_val = await _get(engine, auth, target, OID_SYS_DESCR)
    sys_oid_val = await _get(engine, auth, target, OID_SYS_OBJECT_ID)

    if_descr = await _walk(engine, auth, target, OID_IF_DESCR)
    if_name = await _walk(engine, auth, target, OID_IF_NAME)
    if_type = await _walk(engine, auth, target, OID_IF_TYPE)
    if_mtu = await _walk(engine, auth, target, OID_IF_MTU)
    if_mac = await _walk(engine, auth, target, OID_IF_PHYS_ADDRESS)
    if_admin = await _walk(engine, auth, target, OID_IF_ADMIN_STATUS)
    if_alias = await _walk(engine, auth, target, OID_IF_ALIAS)
    if_speed = await _walk(engine, auth, target, OID_IF_HIGH_SPEED)
    if_duplex = await _walk(engine, auth, target, OID_DOT3_DUPLEX)

    device = DeviceData(
        target=dev.target,
        sys_name=_to_str(sys_name_val) if sys_name_val is not None else None,
        sys_descr=_to_str(sys_descr_val) if sys_descr_val is not None else None,
        # Manual vendor override (from config) wins; else detect from sysObjectID.
        vendor=(dev.vendor or None) or _vendor_from_sysoid(
            _to_str(sys_oid_val) if sys_oid_val is not None else None),
    )

    indexes = {int(i) for i in if_descr if i.isdigit()} | {int(i) for i in if_name if i.isdigit()}
    for idx in sorted(indexes):
        key = str(idx)
        name = _to_str(if_name.get(key, "")).strip() or _to_str(if_descr.get(key, "")).strip()
        if not name:
            continue
        mtu = _to_int(if_mtu.get(key))
        speed_mbps = _to_int(if_speed.get(key))
        if_type_num = _to_int(if_type.get(key)) or 1
        speed_kbps = speed_mbps * 1000 if speed_mbps else None
        device.interfaces[idx] = InterfaceData(
            if_index=idx,
            name=name,
            if_type=if_type_num,
            mtu=mtu if mtu else None,
            mac=_format_mac(if_mac.get(key)) if key in if_mac else None,
            enabled=_to_int(if_admin.get(key)) == 1,
            description=_to_str(if_alias.get(key, "")).strip(),
            speed_kbps=speed_kbps,
            duplex=_DUPLEX_MAP.get(_to_int(if_duplex.get(key))),
            nb_type=netbox_interface_type(if_type_num, speed_kbps, dev.default_ethernet_type,
                                          name=name, vendor=device.vendor),
        )

    ifindex_by_ip = await _walk(engine, auth, target, OID_IP_AD_ENT_IFINDEX)
    mask_by_ip = await _walk(engine, auth, target, OID_IP_AD_ENT_NETMASK)
    for ip, ifindex_val in ifindex_by_ip.items():
        if dev.skip_loopback_ips and _is_skippable_ip(ip):
            continue
        if_index = _to_int(ifindex_val)
        if if_index is None:
            continue
        prefix = None
        if ip in mask_by_ip:
            prefix = _mask_to_prefix(_to_str(mask_by_ip[ip]))
        if prefix is None:
            prefix = 32
        device.ip_addresses.append(IPAddressData(address=f"{ip}/{prefix}", if_index=if_index))

    _assign_parents(device)
    device.vlans = await _collect_vlans(engine, auth, target, device)
    await _collect_port_vlans(engine, auth, target, device)
    device.lldp_neighbors = await _collect_lldp(engine, auth, target, device)
    device.model, device.serial = await _collect_entity_info(engine, auth, target)

    return device


async def collect_with_ping(dev: DeviceConfig, ping_timeout: float = 1.5) -> DeviceData:
    """Quick reachability probe before the full collect, so a dead host fails in ~1.5s
    instead of blocking for the full per-OID timeout × retries. Raises SnmpError if down."""
    alive, err = await quick_snmp_ping(dev, timeout=ping_timeout)
    if not alive:
        raise SnmpError(err or "SNMP unreachable")
    return await collect(dev)


async def _collect_entity_info(engine, auth, target) -> tuple[str | None, str | None]:
    """Best-effort ENTITY-MIB chassis model + serial. Returns (model, serial) or (None, None).

    Picks the row where ``entPhysicalClass == 3`` (chassis); if no chassis row exists, falls
    back to the first physical entry with a non-empty model name. Devices that don't expose
    ENTITY-MIB at all just return (None, None) — non-fatal.
    """
    try:
        classes = await _walk(engine, auth, target, OID_ENT_PHYSICAL_CLASS)
        models = await _walk(engine, auth, target, OID_ENT_PHYSICAL_MODEL)
        serials = await _walk(engine, auth, target, OID_ENT_PHYSICAL_SERIAL)
    except SnmpError:
        return None, None

    # Find chassis row (class=3). entPhysicalIndex is the table key (the OID suffix).
    chassis_idx = next((idx for idx, val in classes.items() if _to_int(val) == 3), None)
    if chassis_idx is None:
        # Fallback: first row that exposes a non-empty model name.
        chassis_idx = next(
            (idx for idx, val in models.items() if _to_str(val).strip()),
            None,
        )
    if chassis_idx is None:
        return None, None
    model = _to_str(models.get(chassis_idx, "")).strip() or None
    serial = _to_str(serials.get(chassis_idx, "")).strip() or None
    return model, serial


def _port_token(name: str) -> str:
    """Interface name without a trailing ' - <comment>' (MikroTik exposes ifName as
    ``<port> - <comment>`` over SNMP, e.g. ``sfp-sfpplus11.2 - UPLINK``). Returns the
    port part used for parent matching; leaves names without the separator untouched."""
    idx = name.find(" - ")
    return name[:idx].rstrip() if idx > 0 else name


def _assign_parents(device: DeviceData) -> None:
    """Derive parent interface for dot-notation sub-interfaces (e.g. bridge.650 -> bridge).

    Matching is done on the comment-stripped port token, so ``sfp-sfpplus11.2 - UPLINK``
    correctly resolves to parent ``sfp-sfpplus11 - (WAN)``. ``parent_name`` is the parent's
    FULL interface name (what NetBox stores), so the syncer can look it up.

    A sub-interface is logical/virtual and must NOT inherit the parent's physical medium type
    (a vendor/speed rule would otherwise type ``sfp-sfpplus11.2`` as ``10gbase-x-sfpp``).
    Force ``virtual`` once we confirm it has a real parent on the device — matching how NetBox
    and the devices themselves (e.g. MikroTik VLANs) model them.
    """
    # port token -> full interface name (for resolving the parent by its real NetBox name)
    by_port = {_port_token(iface.name): iface.name for iface in device.interfaces.values()}
    for iface in device.interfaces.values():
        port = _port_token(iface.name)
        if "." not in port:
            continue
        # A dot in the (comment-stripped) port name denotes a logical sub-interface across
        # MikroTik/Cisco/Huawei. It's virtual regardless of whether its parent port is
        # SNMP-visible — so type it virtual first, then link the parent only if we found it.
        iface.nb_type = "virtual"
        candidate = port.rsplit(".", 1)[0]
        if not candidate or candidate == port:
            continue
        parent_full = by_port.get(candidate)
        if parent_full and parent_full != iface.name:
            iface.parent_name = parent_full


def _vlan_comment(iface_name: str) -> str:
    """The ' - <comment>' suffix MikroTik appends to ifName, e.g.
    ``sfp-sfpplus11.2 - UPLINK`` -> ``UPLINK``. Empty when there's no comment."""
    sep = iface_name.find(" - ")
    return iface_name[sep + 3:].strip() if sep > 0 else ""


async def _collect_vlans(engine, auth, target, device: DeviceData) -> list[VlanData]:
    """802.1Q VLANs from two sources, merged by VID:

    1. Q-BRIDGE-MIB ``dot1qVlanStaticName`` — devices that expose the standard VLAN
       table (typically Cisco). Many routers don't (MikroTik returns nothing).
    2. VLAN sub-interfaces — RouterOS exposes no dot1q VLAN table, but every VLAN is a
       sub-interface named ``<parent>.<vid> - <comment>`` with ifType l2vlan. We parse
       the VID from the (already-collected) interface name and use the comment as the
       VLAN name. This is what finally surfaces VLANs for MikroTik gear.
    """
    by_vid: dict[int, str] = {}

    # Source 1: Q-BRIDGE static VLAN names.
    try:
        rows = await _walk(engine, auth, target, OID_DOT1Q_VLAN_STATIC_NAME)
    except SnmpError:
        rows = {}
    for suffix, value in rows.items():
        vid = _oid_suffix_vlan_id(suffix)
        if vid is None:
            continue
        by_vid[vid] = _to_str(value).strip()

    # Source 2: Cisco VTP VLAN names.
    try:
        rows = await _walk(engine, auth, target, OID_CISCO_VTP_VLAN_NAME)
    except SnmpError:
        rows = {}
    for suffix, value in rows.items():
        vid = _oid_suffix_vlan_id(suffix)
        if vid is None:
            continue
        name = _to_str(value).strip()
        if vid not in by_vid or (not by_vid[vid] and name):
            by_vid[vid] = name

    # Source 3: Q-BRIDGE membership bitmaps also reveal existing VLAN IDs.
    for oid in (
        OID_DOT1Q_VLAN_CURRENT_EGRESS_PORTS,
        OID_DOT1Q_VLAN_CURRENT_UNTAGGED_PORTS,
        OID_DOT1Q_VLAN_STATIC_EGRESS_PORTS,
        OID_DOT1Q_VLAN_STATIC_UNTAGGED_PORTS,
    ):
        try:
            rows = await _walk(engine, auth, target, oid)
        except SnmpError:
            rows = {}
        for suffix in rows:
            vid = _oid_suffix_vlan_id(suffix)
            if vid is not None:
                by_vid.setdefault(vid, "")

    # Source 4: VLAN sub-interfaces (parse VID from the dot-notation port token).
    for iface in device.interfaces.values():
        port = _port_token(iface.name)
        if "." not in port:
            continue
        tail = port.rsplit(".", 1)[1]
        if not tail.isdigit():
            continue
        vid = int(tail)
        comment = _vlan_comment(iface.name)
        # Prefer a non-empty name; don't overwrite an existing name with a blank one.
        if vid not in by_vid or (not by_vid[vid] and comment):
            by_vid[vid] = comment or by_vid.get(vid, "")

    vlans = [VlanData(vid=vid, name=name or f"VLAN{vid}") for vid, name in by_vid.items()]
    vlans.sort(key=lambda v: v.vid)
    return vlans


async def _collect_port_vlans(engine, auth, target, device: DeviceData) -> None:
    """Fill each interface's access_vlan + tagged_vlans (best-effort).

    access_vlan (untagged/native): from Q-BRIDGE ``dot1qPvid`` (bridge-port -> PVID)
      joined with BRIDGE-MIB ``dot1dBasePortIfIndex`` (bridge-port -> ifIndex). MikroTik
      and Cisco both expose these. PVID 1 (the default VLAN) is treated as "no explicit
      access VLAN" and left as None to avoid noise.

    tagged_vlans (trunk): SNMP has no usable per-port egress table on RouterOS, so we
      infer it — each ``<parent>.<vid>`` sub-interface means the parent carries <vid>
      tagged. We attribute those VIDs to the parent interface.
    """
    # --- access VLAN (PVID) ---
    try:
        pvid_by_port = await _walk(engine, auth, target, OID_DOT1Q_PVID)
        ifindex_by_port = await _walk(engine, auth, target, OID_DOT1D_BASE_PORT_IFINDEX)
    except SnmpError:
        pvid_by_port, ifindex_by_port = {}, {}
    for bridge_port, pvid_val in pvid_by_port.items():
        pvid = _to_int(pvid_val)
        if pvid is None or pvid == 1:  # 1 = default VLAN, not an explicit access assignment
            continue
        if_index = _to_int(ifindex_by_port.get(bridge_port))
        if if_index is None:
            continue
        iface = device.interfaces.get(if_index)
        if iface is not None:
            iface.access_vlan = pvid

    # --- tagged VLANs from Q-BRIDGE PortList bitmaps ---
    try:
        current_egress = await _walk(engine, auth, target, OID_DOT1Q_VLAN_CURRENT_EGRESS_PORTS)
    except SnmpError:
        current_egress = {}
    try:
        current_untagged = await _walk(engine, auth, target, OID_DOT1Q_VLAN_CURRENT_UNTAGGED_PORTS)
    except SnmpError:
        current_untagged = {}
    try:
        static_egress = await _walk(engine, auth, target, OID_DOT1Q_VLAN_STATIC_EGRESS_PORTS)
    except SnmpError:
        static_egress = {}
    try:
        static_untagged = await _walk(engine, auth, target, OID_DOT1Q_VLAN_STATIC_UNTAGGED_PORTS)
    except SnmpError:
        static_untagged = {}

    egress_rows = current_egress or static_egress
    untagged_rows = current_untagged or static_untagged
    untagged_by_vid: dict[int, set[int]] = {}
    for suffix, value in untagged_rows.items():
        vid = _oid_suffix_vlan_id(suffix)
        if vid is not None:
            untagged_by_vid.setdefault(vid, set()).update(_port_list_bridge_ports(value))

    tagged_by_ifindex: dict[int, set[int]] = {}
    untagged_by_ifindex: dict[int, set[int]] = {}
    for suffix, value in egress_rows.items():
        vid = _oid_suffix_vlan_id(suffix)
        if vid is None or vid == 1:
            continue
        egress_ports = _port_list_bridge_ports(value)
        untagged_ports = untagged_by_vid.get(vid, set())
        for bridge_port in egress_ports - untagged_ports:
            if_index = _to_int(ifindex_by_port.get(str(bridge_port)))
            if if_index is not None:
                tagged_by_ifindex.setdefault(if_index, set()).add(vid)
        for bridge_port in untagged_ports:
            if_index = _to_int(ifindex_by_port.get(str(bridge_port)))
            if if_index is not None:
                untagged_by_ifindex.setdefault(if_index, set()).add(vid)

    for if_index, vids in untagged_by_ifindex.items():
        iface = device.interfaces.get(if_index)
        if iface is not None and iface.access_vlan is None and len(vids) == 1:
            iface.access_vlan = next(iter(vids))
    for if_index, vids in tagged_by_ifindex.items():
        iface = device.interfaces.get(if_index)
        if iface is not None:
            iface.tagged_vlans = sorted(set(iface.tagged_vlans).union(vids))

    # --- Cisco access and trunk VLANs ---
    try:
        vm_vlan = await _walk(engine, auth, target, OID_CISCO_VM_VLAN)
    except SnmpError:
        vm_vlan = {}
    for suffix, vlan_val in vm_vlan.items():
        if_index = _to_int(suffix)
        vid = _to_int(vlan_val)
        if if_index is None or vid is None or vid in (0, 1):
            continue
        iface = device.interfaces.get(if_index)
        if iface is not None and iface.access_vlan is None:
            iface.access_vlan = vid

    try:
        trunk_status = await _walk(engine, auth, target, OID_CISCO_TRUNK_DYNAMIC_STATUS)
    except SnmpError:
        trunk_status = {}
    trunk_ifindexes = {
        _to_int(suffix)
        for suffix, status in trunk_status.items()
        if _to_int(status) == 1
    }
    trunk_ifindexes.discard(None)
    if trunk_ifindexes:
        known_vlan_ids = {v.vid for v in device.vlans if v.vid not in (0, 1)}
        try:
            native_vlans = await _walk(engine, auth, target, OID_CISCO_TRUNK_NATIVE_VLAN)
        except SnmpError:
            native_vlans = {}
        enabled_specs = (
            (OID_CISCO_TRUNK_VLANS_ENABLED, 0),
            (OID_CISCO_TRUNK_VLANS_ENABLED_2K, 1024),
            (OID_CISCO_TRUNK_VLANS_ENABLED_3K, 2048),
            (OID_CISCO_TRUNK_VLANS_ENABLED_4K, 3072),
        )
        enabled_by_ifindex: dict[int, set[int]] = {}
        for oid, base in enabled_specs:
            try:
                rows = await _walk(engine, auth, target, oid)
            except SnmpError:
                rows = {}
            for suffix, bitmap in rows.items():
                if_index = _to_int(suffix)
                if if_index not in trunk_ifindexes:
                    continue
                enabled = _vlan_list(bitmap, base=base)
                if known_vlan_ids:
                    enabled &= known_vlan_ids
                enabled_by_ifindex.setdefault(if_index, set()).update(enabled)
        for if_index, enabled in enabled_by_ifindex.items():
            iface = device.interfaces.get(if_index)
            if iface is None:
                continue
            native = _to_int(native_vlans.get(str(if_index)))
            if native and native != 1 and iface.access_vlan is None:
                iface.access_vlan = native
            tagged = set(enabled)
            if native:
                tagged.discard(native)
            if tagged:
                iface.tagged_vlans = sorted(set(iface.tagged_vlans).union(tagged))

    # --- tagged VLANs inferred from sub-interfaces ---
    # Map each parent interface NAME -> set of VIDs carried by its sub-interfaces.
    tagged_by_parent: dict[str, set[int]] = {}
    for iface in device.interfaces.values():
        if not iface.parent_name:
            continue
        port = _port_token(iface.name)
        if "." not in port:
            continue
        tail = port.rsplit(".", 1)[1]
        if not tail.isdigit():
            continue
        tagged_by_parent.setdefault(iface.parent_name, set()).add(int(tail))
    for iface in device.interfaces.values():
        vids = tagged_by_parent.get(iface.name)
        if vids:
            iface.tagged_vlans = sorted(vids)


async def _collect_lldp(engine, auth, target, device: DeviceData) -> list[LldpNeighbor]:
    """Best-effort LLDP neighbors. Many devices don't expose LLDP-MIB → []."""
    try:
        loc_port_desc = await _walk(engine, auth, target, OID_LLDP_LOC_PORT_DESC)
        rem_sys_name = await _walk(engine, auth, target, OID_LLDP_REM_SYS_NAME)
        rem_port_desc = await _walk(engine, auth, target, OID_LLDP_REM_PORT_DESC)
        rem_port_id = await _walk(engine, auth, target, OID_LLDP_REM_PORT_ID)
        rem_port_id_subtype = await _walk(engine, auth, target, OID_LLDP_REM_PORT_ID_SUBTYPE)
        rem_chassis_id = await _walk(engine, auth, target, OID_LLDP_REM_CHASSIS_ID)
    except SnmpError:
        return []
    if not rem_sys_name:
        return []

    # Build lookup: local LLDP port number → our ifIndex.
    # The LLDP "local port" id space isn't guaranteed to equal ifIndex, but `lldpLocPortDesc`
    # almost always matches our `ifName` (verified for Mikrotik/Cisco). Match by name.
    name_to_ifindex = {iface.name: iface.if_index for iface in device.interfaces.values()}
    # Also a comment-stripped variant so MikroTik names like "ether1 - WAN" still resolve.
    token_to_ifindex: dict[str, int] = {}
    for iface in device.interfaces.values():
        token_to_ifindex.setdefault(_port_token(iface.name), iface.if_index)

    neighbors: list[LldpNeighbor] = []
    for suffix, rem_name_val in rem_sys_name.items():
        # suffix = "TimeMark.LocalPortNum.RemIndex"
        parts = suffix.split(".")
        if len(parts) != 3:
            continue
        local_port_num = parts[1]

        loc_desc = _to_str(loc_port_desc.get(local_port_num, "")).strip()
        local_ifindex = name_to_ifindex.get(loc_desc) or token_to_ifindex.get(_port_token(loc_desc))
        if local_ifindex is None:
            # Last-ditch: maybe lldpLocPortNum *is* an ifIndex on this vendor.
            try:
                guess = int(local_port_num)
            except ValueError:
                continue
            if guess in device.interfaces:
                local_ifindex = guess
                loc_desc = device.interfaces[guess].name
            else:
                continue

        # Decode the remote port: lldpRemPortDesc is the human-readable one; if empty fall back
        # to the formatted lldpRemPortId. Subtype 3 = MAC, anything else we treat as a string.
        rport_desc = _to_str(rem_port_desc.get(suffix, "")).strip()
        rport_id_subtype = _to_int(rem_port_id_subtype.get(suffix))
        if rport_desc:
            remote_port = rport_desc
        else:
            raw = rem_port_id.get(suffix)
            if rport_id_subtype == 3:
                remote_port = _format_mac(raw) or "?"
            else:
                remote_port = _to_str(raw).strip() or "?"

        chassis_mac = _format_mac(rem_chassis_id.get(suffix))
        remote_system = _to_str(rem_name_val).strip()
        if not remote_system:
            continue

        neighbors.append(LldpNeighbor(
            local_if_index=local_ifindex,
            local_port=loc_desc or device.interfaces[local_ifindex].name,
            remote_system=remote_system,
            remote_port=remote_port,
            remote_chassis_id=chassis_mac,
        ))
    # Stable order: by local port name, then remote system.
    neighbors.sort(key=lambda n: (n.local_port.lower(), n.remote_system.lower()))
    return neighbors

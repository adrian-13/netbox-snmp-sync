"""ORM-based comparison and write engine.

This is the in-NetBox port of the standalone tool's ``netbox_compare`` (read-only diff)
and ``netbox_sync.NetBoxSyncer`` (add-only writer). Instead of talking to NetBox over the
REST API with pynetbox, it reads and writes NetBox objects directly through the Django ORM.

The plugin always knows which ``dcim.Device`` it is syncing (the job runs per
DeviceSNMPConfig), so there's no device-resolution-by-IP step here — the caller passes the
Device object in.

Current scope: interfaces, IPv4 addresses, optional VLAN membership, and reversible
history for objects created by sync runs. LLDP collection is read-only metadata for now.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from dcim.models import Interface, MACAddress
from ipam.models import VLAN, IPAddress

from .dto import DeviceData
from .port_names import normalize_port_name

log = logging.getLogger("netbox_snmp_sync.engine")

# Diff status values
NEW = "new"
EXISTS = "exists"
CHANGED = "changed"
IGNORED = "ignored"


# ─────────────────────────────── result/diff types ───────────────────────────────

@dataclass
class SyncResult:
    interfaces_created: int = 0
    interfaces_existing: int = 0
    interfaces_ignored: int = 0
    interfaces_updated: int = 0
    ips_created: int = 0
    ips_existing: int = 0
    vlans_created: int = 0       # ipam.VLAN objects auto-created
    iface_vlans_set: int = 0     # interfaces whose VLAN membership we wrote
    warnings: list[str] = field(default_factory=list)
    created_objects: list = field(default_factory=list)  # model instances we created (for revert)


@dataclass
class IfaceDiff:
    name: str
    status: str
    type: str
    speed: str
    duplex: str
    description: str
    parent: str = ""
    changes: list[dict] = field(default_factory=list)


@dataclass
class IpDiff:
    address: str
    status: str
    iface: str


@dataclass
class DeviceDiff:
    interfaces: list[IfaceDiff] = field(default_factory=list)
    ips: list[IpDiff] = field(default_factory=list)
    netbox_only_interfaces: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def new_interfaces(self) -> int:
        return sum(1 for i in self.interfaces if i.status == NEW)

    @property
    def changed_interfaces(self) -> int:
        return sum(1 for i in self.interfaces if i.status == CHANGED)

    @property
    def new_ips(self) -> int:
        return sum(1 for i in self.ips if i.status == NEW)


# ─────────────────────────────── helpers ───────────────────────────────

def _ch(name: str, old, new) -> dict:
    return {
        "field": name,
        "old": "—" if old in (None, "") else str(old),
        "new": "—" if new in (None, "") else str(new),
    }


def _iface_changes(iface, rec: Interface, nb_type: str) -> list[dict]:
    """Field-level differences between the SNMP reading and an existing NetBox interface.

    Conservative: only flags a field when SNMP actually reported a value. ``rec`` is a
    Django ``Interface`` (plain string ``type``/``duplex``, FK ``parent``).
    """
    changes: list[dict] = []
    if rec.type and rec.type != nb_type:
        changes.append(_ch("type", rec.type, nb_type))
    if iface.mtu and rec.mtu and iface.mtu != rec.mtu:
        changes.append(_ch("mtu", rec.mtu, iface.mtu))
    if iface.speed_kbps and rec.speed and iface.speed_kbps != rec.speed:
        changes.append(_ch("speed", f"{rec.speed // 1000} Mb", f"{iface.speed_kbps // 1000} Mb"))
    if iface.duplex and rec.duplex and iface.duplex != rec.duplex:
        changes.append(_ch("duplex", rec.duplex, iface.duplex))
    if rec.enabled is not None and iface.enabled != rec.enabled:
        changes.append(_ch("enabled", "up" if rec.enabled else "down", "up" if iface.enabled else "down"))
    if iface.description and iface.description != (rec.description or ""):
        changes.append(_ch("description", rec.description or "", iface.description))
    rec_parent = rec.parent.name if rec.parent else ""
    if iface.parent_name and normalize_port_name(iface.parent_name) != normalize_port_name(rec_parent):
        changes.append(_ch("parent", rec_parent, iface.parent_name))
    return changes


def _existing_interfaces(device):
    """Return (by_norm_name, by_actual_name) maps of the device's current interfaces."""
    recs = list(Interface.objects.filter(device=device))
    by_norm = {normalize_port_name(r.name): r for r in recs}
    by_actual = {r.name: r for r in recs}
    return by_norm, by_actual


# ─────────────────────────────── compare (read-only) ───────────────────────────────

def compare_device(device, data: DeviceData, *, ignore_patterns: tuple[str, ...] = ()) -> DeviceDiff:
    """Read-only diff of SNMP-collected ``data`` against the NetBox ``device``'s state."""
    diff = DeviceDiff()
    ignore_res = [re.compile(p) for p in ignore_patterns]
    by_norm, by_actual = _existing_interfaces(device)

    matched_actual: set[str] = set()
    for iface in data.interfaces.values():
        speed = f"{iface.speed_kbps // 1000} Mb" if iface.speed_kbps else "-"
        row = IfaceDiff(
            name=iface.name, status=NEW, type=iface.nb_type, speed=speed,
            duplex=iface.duplex or "-", description=iface.description,
            parent=iface.parent_name or "",
        )
        rec = by_actual.get(iface.name) or by_norm.get(normalize_port_name(iface.name))
        if any(rx.search(iface.name) for rx in ignore_res):
            row.status = IGNORED
        elif rec is not None:
            row.changes = _iface_changes(iface, rec, iface.nb_type)
            row.status = CHANGED if row.changes else EXISTS
            matched_actual.add(rec.name)
        diff.interfaces.append(row)

    index_to_name = {i.if_index: i.name for i in data.interfaces.values()}
    for ip in data.ip_addresses:
        ifname = index_to_name.get(ip.if_index, f"ifIndex {ip.if_index}")
        status = EXISTS if IPAddress.objects.filter(address=ip.address).exists() else NEW
        diff.ips.append(IpDiff(address=ip.address, status=status, iface=ifname))

    diff.netbox_only_interfaces = sorted(set(by_actual) - matched_actual)
    return diff


# ─────────────────────────────── apply (write, add-only) ───────────────────────────────

def _assign_mac(iface: Interface, mac: str):
    """NetBox >= 4.2: create a MACAddress object and set it as the interface's primary MAC."""
    try:
        mac_obj = MACAddress.objects.create(mac_address=mac, assigned_object=iface)
        iface.primary_mac_address = mac_obj
        iface.save()
    except Exception as exc:  # noqa: BLE001
        log.warning("interface %s: could not set MAC %s: %s", iface.name, mac, exc)


def apply_sync(
    device,
    data: DeviceData,
    *,
    dry_run: bool = False,
    update_existing: bool = False,
    set_mac_address: bool = True,
    write_vlans: bool = False,
    create_vlans: bool = False,
    ignore_patterns: tuple[str, ...] = (),
) -> SyncResult:
    """Add-only sync of ``data`` into the NetBox ``device`` via the ORM.

    Creates missing interfaces (and their MAC/parent) and missing IPs; optionally updates
    changed fields on existing interfaces and writes per-interface VLAN membership. Never
    deletes anything.
    """
    result = SyncResult()
    prefix = "[DRY-RUN] would " if dry_run else ""
    ignore_res = [re.compile(p) for p in ignore_patterns]
    by_norm, by_actual = _existing_interfaces(device)

    name_to_iface: dict[str, Interface] = {}
    for rec in by_actual.values():
        name_to_iface[rec.name] = rec
        name_to_iface.setdefault(normalize_port_name(rec.name), rec)

    valid_iface_names: set[str] = set(by_actual)
    created: dict[str, Interface] = {}
    update_targets: list[tuple] = []

    for iface in data.interfaces.values():
        if any(rx.search(iface.name) for rx in ignore_res):
            result.interfaces_ignored += 1
            continue

        rec = by_actual.get(iface.name) or by_norm.get(normalize_port_name(iface.name))
        if rec is not None:
            if update_existing:
                update_targets.append((iface, rec))
            else:
                result.interfaces_existing += 1
            valid_iface_names.add(rec.name)
            valid_iface_names.add(iface.name)
            name_to_iface.setdefault(iface.name, rec)
            continue

        valid_iface_names.add(iface.name)
        log.info("%screate interface %s (type %s)", prefix, iface.name, iface.nb_type)
        if not dry_run:
            new = Interface(
                device=device,
                name=iface.name,
                type=iface.nb_type,
                enabled=iface.enabled,
                description=iface.description or "",
            )
            if iface.mtu:
                new.mtu = iface.mtu
            if iface.speed_kbps:
                new.speed = iface.speed_kbps
            if iface.duplex:
                new.duplex = iface.duplex
            new.save()
            name_to_iface[iface.name] = new
            name_to_iface.setdefault(normalize_port_name(iface.name), new)
            created[iface.name] = new
            result.created_objects.append(new)
            if set_mac_address and iface.mac:
                _assign_mac(new, iface.mac)
        result.interfaces_created += 1

    _set_parents(data, created, name_to_iface, dry_run)
    _update_existing(update_targets, name_to_iface, result, dry_run, prefix)
    _sync_ips(data, valid_iface_names, name_to_iface, result, dry_run, prefix)
    if write_vlans:
        _sync_iface_vlans(device, data, name_to_iface, result, dry_run=dry_run, create_vlans=create_vlans, prefix=prefix)
    return result


def _sync_iface_vlans(device, data: DeviceData, name_to_iface: dict, result: SyncResult,
                      *, dry_run: bool, create_vlans: bool, prefix: str):
    """Write per-interface VLAN membership (mode + untagged_vlan + tagged_vlans).

    VLANs must already exist in NetBox — unless ``create_vlans`` is set, in which case missing
    VLANs are created in the device's site first. VID is not globally unique, so we prefer the
    device's site and refuse to guess when a VID exists in several scopes.
    """
    needed: set[int] = set()
    for iface in data.interfaces.values():
        if iface.access_vlan:
            needed.add(iface.access_vlan)
        needed.update(iface.tagged_vlans)
    if not needed:
        return

    site = device.site
    site_map: dict[int, VLAN] = {}
    all_map: dict[int, list] = {}
    for v in VLAN.objects.filter(vid__in=needed):
        all_map.setdefault(v.vid, []).append(v)
        if site and v.site_id == site.id:
            site_map[v.vid] = v

    vlan_names = {v.vid: v.name for v in data.vlans}
    vid_to_vlan: dict[int, VLAN] = dict(site_map)
    would_create: set[int] = set()
    for vid in sorted(needed):
        if vid in vid_to_vlan:
            continue
        objs = all_map.get(vid, [])
        if len(objs) == 1:
            vid_to_vlan[vid] = objs[0]
            continue
        if len(objs) > 1:
            continue  # ambiguous across scopes — refuse to guess
        if not create_vlans:
            continue
        vname = vlan_names.get(vid) or f"VLAN{vid}"
        if dry_run:
            result.vlans_created += 1
            would_create.add(vid)
            continue
        try:
            vlan = VLAN(vid=vid, name=vname, site=site)
            vlan.save()
            vid_to_vlan[vid] = vlan
            result.vlans_created += 1
            result.created_objects.append(vlan)
            log.info("%screated VLAN %s (%s)", prefix, vid, vname)
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"VLAN {vid} ({vname}): create failed: {exc}")

    for iface in data.interfaces.values():
        if not iface.access_vlan and not iface.tagged_vlans:
            continue
        rec = name_to_iface.get(iface.name) or name_to_iface.get(normalize_port_name(iface.name))
        if rec is None:
            continue

        desired_untagged = vid_to_vlan.get(iface.access_vlan) if iface.access_vlan else None
        if iface.access_vlan and desired_untagged is None and iface.access_vlan not in would_create:
            result.warnings.append(f"{iface.name}: access VLAN {iface.access_vlan} not found in device site — skipped")
        desired_tagged = []
        for v in iface.tagged_vlans:
            tv = vid_to_vlan.get(v)
            if tv is None:
                if v not in would_create:
                    result.warnings.append(f"{iface.name}: tagged VLAN {v} not found in NetBox — skipped")
            else:
                desired_tagged.append(tv)
        desired_mode = "tagged" if iface.tagged_vlans else ("access" if iface.access_vlan else None)
        if desired_mode is None or (desired_untagged is None and not desired_tagged):
            continue

        log.info("%sset VLANs on %s (mode=%s, untagged=%s, tagged=%s)",
                 prefix, iface.name, desired_mode, iface.access_vlan or "-", iface.tagged_vlans or "-")
        if dry_run:
            result.iface_vlans_set += 1
            continue
        try:
            changed = False
            if rec.mode != desired_mode:
                rec.mode = desired_mode
                changed = True
            if desired_untagged is not None and rec.untagged_vlan_id != desired_untagged.pk:
                rec.untagged_vlan = desired_untagged
                changed = True
            if changed:
                rec.save()
            if desired_tagged:
                want = sorted(v.pk for v in desired_tagged)
                cur = sorted(rec.tagged_vlans.values_list("pk", flat=True))
                if cur != want:
                    rec.tagged_vlans.set(desired_tagged)
                    changed = True
            if changed:
                result.iface_vlans_set += 1
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"{iface.name}: VLAN write failed: {exc}")


def _set_parents(data: DeviceData, created: dict, name_to_iface: dict, dry_run: bool):
    """Second pass: link newly created sub-interfaces to their parent interface."""
    for iface in data.interfaces.values():
        rec = created.get(iface.name)
        if rec is None or not iface.parent_name:
            continue
        parent = name_to_iface.get(iface.parent_name) or name_to_iface.get(normalize_port_name(iface.parent_name))
        if not parent or parent.pk == rec.pk:
            continue
        if not dry_run:
            try:
                rec.parent = parent
                rec.save()
            except Exception as exc:  # noqa: BLE001
                log.warning("interface %s: could not set parent %s: %s", iface.name, iface.parent_name, exc)


def _update_existing(update_targets, name_to_iface, result: SyncResult, dry_run: bool, prefix: str):
    for iface, rec in update_targets:
        changed = False
        if rec.type != iface.nb_type:
            rec.type = iface.nb_type
            changed = True
        if iface.mtu and rec.mtu != iface.mtu:
            rec.mtu = iface.mtu
            changed = True
        if iface.speed_kbps and rec.speed != iface.speed_kbps:
            rec.speed = iface.speed_kbps
            changed = True
        if iface.duplex and rec.duplex != iface.duplex:
            rec.duplex = iface.duplex
            changed = True
        if rec.enabled != iface.enabled:
            rec.enabled = iface.enabled
            changed = True
        if iface.description and (rec.description or "") != iface.description:
            rec.description = iface.description
            changed = True
        if iface.parent_name:
            parent = name_to_iface.get(iface.parent_name) or name_to_iface.get(normalize_port_name(iface.parent_name))
            if parent and (rec.parent_id != parent.pk):
                rec.parent = parent
                changed = True
        if not changed:
            result.interfaces_existing += 1
            continue
        log.info("%supdate interface %s", prefix, iface.name)
        if not dry_run:
            try:
                rec.save()
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"{iface.name}: update failed: {exc}")
                continue
        result.interfaces_updated += 1


def _sync_ips(data: DeviceData, valid_iface_names: set[str], name_to_iface: dict,
              result: SyncResult, dry_run: bool, prefix: str):
    index_to_name = {iface.if_index: iface.name for iface in data.interfaces.values()}
    for ip in data.ip_addresses:
        iface_name = index_to_name.get(ip.if_index)
        if iface_name is None:
            result.warnings.append(f"IP {ip.address} on unknown ifIndex {ip.if_index}")
            continue
        if iface_name not in valid_iface_names:
            result.warnings.append(f"IP {ip.address} skipped — interface {iface_name!r} ignored/not synced")
            continue
        if IPAddress.objects.filter(address=ip.address).exists():
            result.ips_existing += 1
            continue
        log.info("%screate IP %s on %s", prefix, ip.address, iface_name)
        if not dry_run:
            iface = name_to_iface.get(iface_name) or name_to_iface.get(normalize_port_name(iface_name))
            ip_obj = IPAddress.objects.create(address=ip.address, status="active", assigned_object=iface)
            result.created_objects.append(ip_obj)
        result.ips_created += 1

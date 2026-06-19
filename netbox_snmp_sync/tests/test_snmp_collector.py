import asyncio
from unittest.mock import patch

from django.test import SimpleTestCase

from netbox_snmp_sync.dto import DeviceData, InterfaceData, VlanData
from netbox_snmp_sync import snmp_collector


class VLANCollectionTestCase(SimpleTestCase):
    def test_collects_vlans_from_q_bridge_and_subinterfaces(self):
        device = DeviceData(target="10.0.0.1")
        device.interfaces[1] = InterfaceData(if_index=1, name="ether1", if_type=6)
        device.interfaces[2] = InterfaceData(if_index=2, name="bridge", if_type=209)
        device.interfaces[3] = InterfaceData(if_index=3, name="bridge.20 - Guests", if_type=135)
        device.interfaces[4] = InterfaceData(if_index=4, name="ether1.30", if_type=135)

        async def fake_walk(_engine, _auth, _target, oid):
            if oid == snmp_collector.OID_DOT1Q_VLAN_STATIC_NAME:
                return {
                    "10": "Users",
                    "20": "",
                }
            return {}

        with patch("netbox_snmp_sync.snmp_collector._walk", new=fake_walk):
            vlans = asyncio.run(snmp_collector._collect_vlans(None, None, None, device))

        self.assertEqual([(v.vid, v.name) for v in vlans], [
            (10, "Users"),
            (20, "Guests"),
            (30, "VLAN30"),
        ])

    def test_collects_cisco_vlan_names_from_vtp_mib(self):
        device = DeviceData(target="10.0.0.1")

        async def fake_walk(_engine, _auth, _target, oid):
            if oid == snmp_collector.OID_CISCO_VTP_VLAN_NAME:
                return {
                    "1.10": "Users",
                    "1.20": "Voice",
                }
            return {}

        with patch("netbox_snmp_sync.snmp_collector._walk", new=fake_walk):
            vlans = asyncio.run(snmp_collector._collect_vlans(None, None, None, device))

        self.assertEqual([(v.vid, v.name) for v in vlans], [
            (10, "Users"),
            (20, "Voice"),
        ])

    def test_collects_vlan_ids_from_q_bridge_port_lists_without_names(self):
        device = DeviceData(target="10.0.0.1")

        async def fake_walk(_engine, _auth, _target, oid):
            if oid == snmp_collector.OID_DOT1Q_VLAN_CURRENT_EGRESS_PORTS:
                return {
                    "0.45": b"\x80",
                    "0.80": b"\x80",
                }
            return {}

        with patch("netbox_snmp_sync.snmp_collector._walk", new=fake_walk):
            vlans = asyncio.run(snmp_collector._collect_vlans(None, None, None, device))

        self.assertEqual([(v.vid, v.name) for v in vlans], [
            (45, "VLAN45"),
            (80, "VLAN80"),
        ])

    def test_collects_access_and_tagged_vlans_for_interfaces(self):
        device = DeviceData(target="10.0.0.1")
        device.interfaces[1] = InterfaceData(if_index=1, name="ether1", if_type=6)
        device.interfaces[2] = InterfaceData(if_index=2, name="bridge", if_type=209)
        device.interfaces[3] = InterfaceData(if_index=3, name="bridge.20 - Guests", if_type=135)
        device.interfaces[4] = InterfaceData(if_index=4, name="ether1.30", if_type=135)
        snmp_collector._assign_parents(device)

        async def fake_walk(_engine, _auth, _target, oid):
            if oid == snmp_collector.OID_DOT1Q_PVID:
                return {
                    "7": 20,
                    "8": 1,
                }
            if oid == snmp_collector.OID_DOT1D_BASE_PORT_IFINDEX:
                return {
                    "7": 1,
                    "8": 2,
                }
            return {}

        with patch("netbox_snmp_sync.snmp_collector._walk", new=fake_walk):
            asyncio.run(snmp_collector._collect_port_vlans(None, None, None, device))

        self.assertEqual(device.interfaces[1].access_vlan, 20)
        self.assertEqual(device.interfaces[1].tagged_vlans, [30])
        self.assertIsNone(device.interfaces[2].access_vlan)
        self.assertEqual(device.interfaces[2].tagged_vlans, [20])

    def test_collects_cisco_tagged_vlans_from_q_bridge_port_lists(self):
        device = DeviceData(target="10.0.0.1")
        device.interfaces[101] = InterfaceData(if_index=101, name="GigabitEthernet1/0/1", if_type=6)
        device.interfaces[102] = InterfaceData(if_index=102, name="GigabitEthernet1/0/2", if_type=6)

        async def fake_walk(_engine, _auth, _target, oid):
            if oid == snmp_collector.OID_DOT1D_BASE_PORT_IFINDEX:
                return {
                    "1": 101,
                    "2": 102,
                }
            if oid == snmp_collector.OID_DOT1Q_PVID:
                return {}
            if oid == snmp_collector.OID_DOT1Q_VLAN_CURRENT_EGRESS_PORTS:
                return {
                    "0.10": b"\xC0",  # bridge ports 1 and 2
                    "0.20": b"\x80",  # bridge port 1
                }
            if oid == snmp_collector.OID_DOT1Q_VLAN_CURRENT_UNTAGGED_PORTS:
                return {
                    "0.10": b"\x40",  # bridge port 2 is untagged in VLAN 10
                }
            return {}

        with patch("netbox_snmp_sync.snmp_collector._walk", new=fake_walk):
            asyncio.run(snmp_collector._collect_port_vlans(None, None, None, device))

        self.assertEqual(device.interfaces[101].tagged_vlans, [10, 20])
        self.assertIsNone(device.interfaces[101].access_vlan)
        self.assertEqual(device.interfaces[102].tagged_vlans, [])
        self.assertEqual(device.interfaces[102].access_vlan, 10)

    def test_collects_cisco_access_vlans_from_vlan_membership_mib(self):
        device = DeviceData(target="10.0.0.1")
        device.interfaces[10101] = InterfaceData(if_index=10101, name="GigabitEthernet1/0/1", if_type=6)

        async def fake_walk(_engine, _auth, _target, oid):
            if oid == snmp_collector.OID_CISCO_VM_VLAN:
                return {
                    "10101": 483,
                }
            return {}

        with patch("netbox_snmp_sync.snmp_collector._walk", new=fake_walk):
            asyncio.run(snmp_collector._collect_port_vlans(None, None, None, device))

        self.assertEqual(device.interfaces[10101].access_vlan, 483)

    def test_collects_cisco_trunk_vlans_from_vtp_trunk_mib(self):
        device = DeviceData(target="10.0.0.1")
        device.vlans.extend([
            VlanData(vid=10, name="Users"),
            VlanData(vid=20, name="Voice"),
            VlanData(vid=30, name="Unused"),
        ])
        device.interfaces[10103] = InterfaceData(if_index=10103, name="GigabitEthernet1/0/3", if_type=6)
        device.interfaces[10104] = InterfaceData(if_index=10104, name="GigabitEthernet1/0/4", if_type=6)

        async def fake_walk(_engine, _auth, _target, oid):
            if oid == snmp_collector.OID_CISCO_TRUNK_DYNAMIC_STATUS:
                return {
                    "10103": 1,
                    "10104": 2,
                }
            if oid == snmp_collector.OID_CISCO_TRUNK_NATIVE_VLAN:
                return {
                    "10103": 10,
                    "10104": 1,
                }
            if oid == snmp_collector.OID_CISCO_TRUNK_VLANS_ENABLED:
                return {
                    "10103": b"\x00\x20\x08",  # VLANs 10 and 20
                    "10104": b"\xff\xff\xff",  # ignored: not trunking
                }
            return {}

        with patch("netbox_snmp_sync.snmp_collector._walk", new=fake_walk):
            asyncio.run(snmp_collector._collect_port_vlans(None, None, None, device))

        self.assertEqual(device.interfaces[10103].access_vlan, 10)
        self.assertEqual(device.interfaces[10103].tagged_vlans, [20])
        self.assertIsNone(device.interfaces[10104].access_vlan)
        self.assertEqual(device.interfaces[10104].tagged_vlans, [])

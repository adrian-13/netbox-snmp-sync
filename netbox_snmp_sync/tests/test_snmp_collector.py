import asyncio
from unittest.mock import patch

from django.test import SimpleTestCase

from netbox_snmp_sync.dto import DeviceData, InterfaceData
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

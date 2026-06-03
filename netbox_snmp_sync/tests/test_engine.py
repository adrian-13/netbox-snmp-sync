from django.test import TestCase

from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from ipam.models import VLAN, IPAddress

from netbox_snmp_sync import engine
from netbox_snmp_sync.dto import DeviceData, InterfaceData, IPAddressData, VlanData
from netbox_snmp_sync.models import DeviceSNMPConfig


def _device_data():
    data = DeviceData(target="10.0.0.1", sys_name="sw1")
    data.interfaces[1] = InterfaceData(
        if_index=1, name="ether1", if_type=6, mtu=1500, mac="AA:BB:CC:DD:EE:01",
        enabled=True, description="uplink", speed_kbps=1_000_000, duplex="full", nb_type="1000base-t",
    )
    data.interfaces[2] = InterfaceData(
        if_index=2, name="bridge", if_type=209, enabled=True, nb_type="bridge",
    )
    data.interfaces[3] = InterfaceData(
        if_index=3, name="bridge.10", if_type=53, enabled=True, nb_type="virtual", parent_name="bridge",
    )
    data.ip_addresses.append(IPAddressData(address="10.0.0.1/30", if_index=1))
    return data


class EngineTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Lab", slug="lab")
        mf = Manufacturer.objects.create(name="MikroTik", slug="mikrotik")
        dt = DeviceType.objects.create(manufacturer=mf, model="CRS", slug="crs")
        role = DeviceRole.objects.create(name="Switch", slug="switch")
        cls.device = Device.objects.create(name="sw1", device_type=dt, role=role, site=site)

    def test_compare_reports_new_objects(self):
        diff = engine.compare_device(self.device, _device_data())
        self.assertEqual(diff.new_interfaces, 3)
        self.assertEqual(diff.new_ips, 1)

    def test_apply_creates_objects(self):
        result = engine.apply_sync(self.device, _device_data(), dry_run=False)
        self.assertEqual(result.interfaces_created, 3)
        self.assertEqual(result.ips_created, 1)
        self.assertEqual(Interface.objects.filter(device=self.device).count(), 3)
        self.assertTrue(IPAddress.objects.filter(address="10.0.0.1/30").exists())

    def test_apply_sets_subinterface_parent(self):
        engine.apply_sync(self.device, _device_data(), dry_run=False)
        sub = Interface.objects.get(device=self.device, name="bridge.10")
        self.assertIsNotNone(sub.parent)
        self.assertEqual(sub.parent.name, "bridge")

    def test_apply_sets_mac(self):
        engine.apply_sync(self.device, _device_data(), dry_run=False)
        eth1 = Interface.objects.get(device=self.device, name="ether1")
        self.assertIsNotNone(eth1.primary_mac_address)
        self.assertEqual(str(eth1.primary_mac_address.mac_address), "AA:BB:CC:DD:EE:01")

    def test_apply_is_idempotent(self):
        engine.apply_sync(self.device, _device_data(), dry_run=False)
        result = engine.apply_sync(self.device, _device_data(), dry_run=False)
        self.assertEqual(result.interfaces_created, 0)
        self.assertEqual(result.ips_created, 0)
        self.assertEqual(result.interfaces_existing, 3)

    def test_dry_run_writes_nothing(self):
        result = engine.apply_sync(self.device, _device_data(), dry_run=True)
        self.assertEqual(result.interfaces_created, 3)
        self.assertEqual(Interface.objects.filter(device=self.device).count(), 0)

    def test_vlan_membership(self):
        VLAN.objects.create(vid=10, name="ten", site=self.device.site)
        VLAN.objects.create(vid=20, name="twenty", site=self.device.site)
        data = DeviceData(target="x", sys_name="sw1")
        data.interfaces[1] = InterfaceData(
            if_index=1, name="ether1", if_type=6, enabled=True, nb_type="1000base-t", access_vlan=10,
        )
        data.interfaces[2] = InterfaceData(
            if_index=2, name="bridge", if_type=209, enabled=True, nb_type="bridge", tagged_vlans=[10, 20],
        )
        result = engine.apply_sync(self.device, data, dry_run=False, write_vlans=True)
        self.assertEqual(result.iface_vlans_set, 2)
        eth1 = Interface.objects.get(device=self.device, name="ether1")
        self.assertEqual(eth1.mode, "access")
        self.assertEqual(eth1.untagged_vlan.vid, 10)
        bridge = Interface.objects.get(device=self.device, name="bridge")
        self.assertEqual(bridge.mode, "tagged")
        self.assertEqual(sorted(bridge.tagged_vlans.values_list("vid", flat=True)), [10, 20])

    def test_revert_run_deletes_created_objects(self):
        from netbox_snmp_sync.models import SyncRun, record_created_objects

        result = engine.apply_sync(self.device, _device_data(), dry_run=False)
        run = SyncRun.objects.create(device=self.device, mode="apply", trigger="manual", status="ok")
        record_created_objects(run, result.created_objects)
        self.assertEqual(run.created_objects.count(), 4)  # 3 interfaces + 1 IP
        self.assertTrue(run.can_revert)

        deleted = run.revert()
        run.refresh_from_db()
        self.assertEqual(deleted, 4)
        self.assertTrue(run.reverted)
        self.assertFalse(run.can_revert)
        self.assertEqual(Interface.objects.filter(device=self.device).count(), 0)
        self.assertFalse(IPAddress.objects.filter(address="10.0.0.1/30").exists())

    def test_create_missing_vlans(self):
        data = DeviceData(target="x", sys_name="sw1")
        data.vlans.append(VlanData(vid=30, name="mgmt"))
        data.interfaces[1] = InterfaceData(
            if_index=1, name="ether1", if_type=6, enabled=True, nb_type="1000base-t", access_vlan=30,
        )
        result = engine.apply_sync(self.device, data, dry_run=False, write_vlans=True, create_vlans=True)
        self.assertEqual(result.vlans_created, 1)
        self.assertTrue(VLAN.objects.filter(vid=30, site=self.device.site).exists())
        eth1 = Interface.objects.get(device=self.device, name="ether1")
        self.assertEqual(eth1.untagged_vlan.vid, 30)


class DeviceSNMPConfigTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Lab", slug="lab")
        mf = Manufacturer.objects.create(name="MikroTik", slug="mikrotik")
        dt = DeviceType.objects.create(manufacturer=mf, model="CRS", slug="crs")
        role = DeviceRole.objects.create(name="Switch", slug="switch")
        cls.device = Device.objects.create(name="sw1", device_type=dt, role=role, site=site)

    def test_to_spec_uses_override_target(self):
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public", port=1161, target_override="snmpsim",
        )
        spec = cfg.to_spec()
        self.assertEqual(spec.target, "snmpsim")
        self.assertEqual(spec.snmp_port, 1161)
        self.assertEqual(spec.snmp_community, "public")

    def test_target_falls_back_when_no_primary_ip(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        self.assertEqual(cfg.target, "")

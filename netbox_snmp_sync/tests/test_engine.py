import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Job
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from ipam.models import VLAN, IPAddress

from netbox_snmp_sync import engine, views
from netbox_snmp_sync.dto import DeviceData, InterfaceData, IPAddressData, VlanData
from netbox_snmp_sync.forms import DeviceSNMPConfigForm, SNMPSyncConfigForm
from netbox_snmp_sync.jobs import (
    SYSTEM_USERNAME,
    PruneSyncRunsJob,
    ScheduledSNMPSyncJob,
    _collect_with_job_timeout,
    _fake_request,
    _sync_one,
)
from netbox_snmp_sync.models import DeviceSNMPConfig, SNMPSyncConfig, SyncRun


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

    def test_compare_marks_unassigned_existing_ip_changed(self):
        Interface.objects.create(device=self.device, name="ether1", type="1000base-t", enabled=True)
        IPAddress.objects.create(address="10.0.0.1/30", status="active")

        diff = engine.compare_device(self.device, _device_data())

        row = next(ip for ip in diff.ips if ip.address == "10.0.0.1/30")
        self.assertEqual(row.status, engine.CHANGED)
        self.assertEqual(row.iface, "ether1")

    def test_compare_marks_existing_interface_without_mac_changed(self):
        Interface.objects.create(device=self.device, name="ether1", type="1000base-t", enabled=True)

        diff = engine.compare_device(self.device, _device_data())

        row = next(iface for iface in diff.interfaces if iface.name == "ether1")
        self.assertEqual(row.status, engine.CHANGED)
        self.assertTrue(any(change["field"] == "primary_mac_address" for change in row.changes))

    def test_apply_creates_objects(self):
        result = engine.apply_sync(self.device, _device_data(), dry_run=False)
        self.assertEqual(result.interfaces_created, 3)
        self.assertEqual(result.ips_created, 1)
        self.assertEqual(Interface.objects.filter(device=self.device).count(), 3)
        self.assertTrue(IPAddress.objects.filter(address="10.0.0.1/30").exists())

    def test_compare_can_disable_interfaces_and_ips(self):
        Interface.objects.create(device=self.device, name="netbox-only", type="1000base-t", enabled=True)

        diff = engine.compare_device(
            self.device,
            _device_data(),
            sync_interfaces=False,
            sync_ip_addresses=False,
        )

        self.assertEqual(diff.interfaces, [])
        self.assertEqual(diff.ips, [])
        self.assertEqual(diff.netbox_only_interfaces, [])

    def test_apply_can_disable_interfaces_and_ips(self):
        result = engine.apply_sync(
            self.device,
            _device_data(),
            dry_run=False,
            sync_interfaces=False,
            sync_ip_addresses=False,
        )

        self.assertEqual(result.interfaces_created, 0)
        self.assertEqual(result.ips_created, 0)
        self.assertEqual(Interface.objects.filter(device=self.device).count(), 0)
        self.assertFalse(IPAddress.objects.filter(address="10.0.0.1/30").exists())

    def test_apply_syncs_ip_to_existing_interface_when_interface_sync_disabled(self):
        iface = Interface.objects.create(device=self.device, name="ether1", type="1000base-t", enabled=True)

        result = engine.apply_sync(
            self.device,
            _device_data(),
            dry_run=False,
            sync_interfaces=False,
            sync_ip_addresses=True,
        )

        ip = IPAddress.objects.get(address="10.0.0.1/30")
        self.assertEqual(ip.assigned_object, iface)
        self.assertEqual(result.interfaces_created, 0)
        self.assertEqual(result.ips_created, 1)

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

    def test_apply_sets_mac_on_existing_interface(self):
        Interface.objects.create(device=self.device, name="ether1", type="1000base-t", enabled=True)

        result = engine.apply_sync(self.device, _device_data(), dry_run=False)

        eth1 = Interface.objects.get(device=self.device, name="ether1")
        self.assertIsNotNone(eth1.primary_mac_address)
        self.assertEqual(str(eth1.primary_mac_address.mac_address), "AA:BB:CC:DD:EE:01")
        self.assertTrue(any(
            change.object_type == "interface"
            and change.object_repr == "ether1"
            and change.field == "primary_mac_address"
            for change in result.changes
        ))

    def test_apply_assigns_existing_unassigned_ip_to_interface(self):
        iface = Interface.objects.create(device=self.device, name="ether1", type="1000base-t", enabled=True)
        ip = IPAddress.objects.create(address="10.0.0.1/30", status="active")

        result = engine.apply_sync(self.device, _device_data(), dry_run=False)

        ip.refresh_from_db()
        self.assertEqual(ip.assigned_object, iface)
        self.assertEqual(result.ips_existing, 1)
        self.assertTrue(any(
            change.object_type == "ipaddress"
            and change.object_repr == "10.0.0.1/30"
            and change.field == "interface"
            and change.new_value == "ether1"
            for change in result.changes
        ))

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

    def test_apply_can_rename_device_to_sysname(self):
        data = _device_data()
        data.sys_name = "sw1-snmp"

        result = engine.apply_sync(self.device, data, dry_run=False, rename_device_to_sysname=True)

        self.device.refresh_from_db()
        self.assertEqual(self.device.name, "sw1-snmp")
        self.assertEqual(result.devices_updated, 1)
        self.assertTrue(any(
            change.object_type == "device"
            and change.field == "name"
            and change.old_value == "sw1"
            and change.new_value == "sw1-snmp"
            for change in result.changes
        ))

    def test_apply_does_not_rename_device_without_flag(self):
        data = _device_data()
        data.sys_name = "sw1-snmp"

        result = engine.apply_sync(self.device, data, dry_run=False)

        self.device.refresh_from_db()
        self.assertEqual(self.device.name, "sw1")
        self.assertEqual(result.devices_updated, 0)

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

    def test_create_vlan_in_device_site_when_vid_exists_elsewhere(self):
        other_site = Site.objects.create(name="Other", slug="other")
        VLAN.objects.create(vid=30, name="other-site-vlan", site=other_site)
        data = DeviceData(target="x", sys_name="sw1")
        data.vlans.append(VlanData(vid=30, name="mgmt"))
        data.interfaces[1] = InterfaceData(
            if_index=1, name="ether1", if_type=6, enabled=True, nb_type="1000base-t", access_vlan=30,
        )

        result = engine.apply_sync(self.device, data, dry_run=False, write_vlans=True, create_vlans=True)

        self.assertEqual(result.vlans_created, 1)
        self.assertTrue(VLAN.objects.filter(vid=30, name="mgmt", site=self.device.site).exists())
        eth1 = Interface.objects.get(device=self.device, name="ether1")
        self.assertEqual(eth1.untagged_vlan.site, self.device.site)

    def test_subinterface_vlan_write_uses_collected_vlan_not_name_suffix(self):
        data = DeviceData(target="x", sys_name="sw1", vendor="Cisco")
        data.vlans.append(VlanData(vid=30, name="Customer"))
        data.interfaces[1] = InterfaceData(
            if_index=1, name="GigabitEthernet0/0/0", if_type=6, enabled=True, nb_type="1000base-t",
        )
        data.interfaces[2] = InterfaceData(
            if_index=2,
            name="GigabitEthernet0/0/0.10",
            if_type=53,
            enabled=True,
            nb_type="virtual",
            parent_name="GigabitEthernet0/0/0",
            access_vlan=30,
        )

        result = engine.apply_sync(self.device, data, dry_run=False, write_vlans=True, create_vlans=True)

        self.assertEqual(result.vlans_created, 1)
        self.assertTrue(VLAN.objects.filter(vid=30, site=self.device.site).exists())
        self.assertFalse(VLAN.objects.filter(vid=10, site=self.device.site).exists())
        sub = Interface.objects.get(device=self.device, name="GigabitEthernet0/0/0.10")
        self.assertEqual(sub.untagged_vlan.vid, 30)


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

    def test_effective_sync_behaviour_uses_global_defaults_and_device_overrides(self):
        settings = SNMPSyncConfig.get()
        settings.sync_interfaces = True
        settings.sync_ip_addresses = True
        settings.update_existing = False
        settings.set_mac_address = True
        settings.write_vlans = False
        settings.create_vlans = False
        settings.vlan_subinterface_inference = "auto"
        settings.save()
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device,
            snmp_version="2c",
            community="public",
            sync_interfaces=False,
            write_vlans=True,
        )

        self.assertEqual(cfg.get_effective_sync_behaviour(), {
            "sync_interfaces": False,
            "sync_ip_addresses": True,
            "update_existing": False,
            "set_mac_address": True,
            "write_vlans": True,
            "create_vlans": False,
        })
        self.assertEqual(cfg.to_spec().vlan_subinterface_inference, "auto")

        cfg.vlan_subinterface_inference = "disabled"
        cfg.save()
        self.assertEqual(cfg.to_spec().vlan_subinterface_inference, "disabled")

    def test_job_collection_timeout_raises_clear_error(self):
        async def slow_collect(_spec):
            await asyncio.sleep(1)

        with patch("netbox_snmp_sync.jobs.collect_with_ping", side_effect=slow_collect):
            with self.assertRaisesRegex(TimeoutError, "timed out after 0.01 seconds"):
                asyncio.run(_collect_with_job_timeout(object(), 0.01))

    def test_apply_job_rolls_back_partial_writes_when_recording_fails(self):
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device,
            snmp_version="2c",
            community="public",
            target_override="10.0.0.1",
        )

        async def fake_collect(_spec):
            return _device_data()

        def partial_apply(device, *_args, **_kwargs):
            iface = Interface.objects.create(device=device, name="partial", type="1000base-t", enabled=True)
            return SimpleNamespace(
                interfaces_created=1,
                interfaces_updated=0,
                interfaces_existing=0,
                interfaces_ignored=0,
                ips_created=0,
                ips_existing=0,
                vlans_created=0,
                iface_vlans_set=0,
                devices_updated=0,
                warnings=[],
                created_objects=[iface],
                changes=[],
            )

        with patch("netbox_snmp_sync.jobs.collect_with_ping", side_effect=fake_collect):
            with patch("netbox_snmp_sync.jobs.engine.apply_sync", side_effect=partial_apply):
                with patch(
                    "netbox_snmp_sync.models.record_created_objects",
                    side_effect=RuntimeError("forced record failure"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "forced record failure"):
                        _sync_one(cfg, mode="apply", trigger="manual")

        self.assertFalse(Interface.objects.filter(device=self.device, name="partial").exists())
        self.assertFalse(SyncRun.objects.filter(device=self.device, status="ok").exists())

    def test_scheduled_fake_request_uses_service_user(self):
        request = _fake_request(None)

        self.assertEqual(request.user.username, SYSTEM_USERNAME)
        self.assertFalse(request.user.is_active)

    def test_next_sync_uses_interval_setting(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8)
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        ref = timezone.now()
        self.assertEqual(cfg.get_next_sync_time(ref), ref + timedelta(hours=8))

    def test_scheduled_sync_advances_next_sync(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8)
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        run = SyncRun.objects.create(device=self.device, trigger="scheduled", mode="apply", status="ok")

        cfg.record_sync_result(run, update_schedule=True)
        cfg.refresh_from_db()

        self.assertEqual(cfg.last_sync_status, "ok")
        self.assertIsNotNone(cfg.next_sync_at)
        self.assertGreater(cfg.next_sync_at, cfg.last_sync_at)

    def test_manual_sync_does_not_reset_next_sync(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8)
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public",
            next_sync_at=timezone.now() + timedelta(hours=2),
        )
        original_next = cfg.next_sync_at
        run = SyncRun.objects.create(device=self.device, trigger="manual", mode="apply", status="ok")

        cfg.record_sync_result(run, update_schedule=False)
        cfg.refresh_from_db()

        self.assertEqual(cfg.next_sync_at, original_next)

    def test_manual_sync_can_reset_next_sync(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8)
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public",
            next_sync_at=timezone.now() + timedelta(hours=2),
        )
        run = SyncRun.objects.create(device=self.device, trigger="manual", mode="apply", status="ok")

        cfg.record_sync_result(run, update_schedule=True)
        cfg.refresh_from_db()

        self.assertEqual(cfg.next_sync_at, cfg.last_sync_at + timedelta(hours=8))

    def test_failed_scheduled_sync_uses_retry_backoff(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8)
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        run = SyncRun.objects.create(device=self.device, trigger="scheduled", mode="apply", status="failed")

        cfg.record_sync_result(run, update_schedule=True)
        cfg.refresh_from_db()

        self.assertEqual(cfg.consecutive_sync_failures, 1)
        self.assertEqual(cfg.next_sync_at, cfg.last_sync_at + timedelta(hours=1))
        self.assertEqual(cfg.sync_state, "retry")
        self.assertEqual(cfg.sync_state_label, "Retry")

    def test_failed_scheduled_sync_due_shows_retry_due(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8)
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        run = SyncRun.objects.create(device=self.device, trigger="scheduled", mode="apply", status="failed")
        cfg.record_sync_result(run, update_schedule=True)
        cfg.next_sync_at = timezone.now() - timedelta(minutes=1)
        cfg.save(update_fields=("next_sync_at",))

        self.assertEqual(cfg.sync_state, "retry_due")
        self.assertEqual(cfg.sync_state_label, "Retry due")

    def test_fixed_hour_sync_uses_next_configured_hour(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8, sync_at_hours="3,15")
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        ref = timezone.now().replace(hour=4, minute=30, second=0, microsecond=0)

        next_sync = cfg.get_next_sync_time(ref)

        self.assertEqual(timezone.localtime(next_sync).hour, 15)
        self.assertGreater(next_sync, ref)

    def test_per_device_interval_overrides_global_fixed_hours(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=24, sync_at_hours="3,15")
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public", sync_interval_hours=8,
        )
        ref = timezone.now().replace(hour=4, minute=30, second=0, microsecond=0)

        self.assertEqual(cfg.get_effective_sync_interval_hours(), 8)
        self.assertEqual(cfg.get_allowed_sync_hours(), set())
        self.assertEqual(cfg.get_next_sync_time(ref), ref + timedelta(hours=8))

    def test_per_device_fixed_hours_override_interval(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8)
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public",
            sync_interval_hours=4, sync_at_hours="15,3",
        )
        ref = timezone.now().replace(hour=4, minute=30, second=0, microsecond=0)

        next_sync = cfg.get_next_sync_time(ref)

        self.assertEqual(cfg.sync_at_hours, "3,15")
        self.assertEqual(timezone.localtime(next_sync).hour, 15)
        self.assertGreater(next_sync, ref)

    def test_per_device_zero_interval_disables_global_schedule(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8)
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public", sync_interval_hours=0,
        )

        self.assertFalse(cfg.is_schedule_enabled())
        self.assertIsNone(cfg.get_next_sync_time(timezone.now()))
        self.assertEqual(cfg.schedule_label, "Disabled")
        self.assertEqual(cfg.sync_state, "disabled")
        self.assertEqual(cfg.sync_state_label, "Disabled")

    def test_scheduler_does_not_queue_disabled_per_device_schedule(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=8)
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public",
            target_override="10.0.0.1", sync_interval_hours=0,
            next_sync_at=timezone.now() - timedelta(hours=1),
        )
        runner = ScheduledSNMPSyncJob(SimpleNamespace(user=None))
        runner.logger = SimpleNamespace(info=lambda _msg: None)

        with patch("netbox_snmp_sync.jobs.SNMPSyncJob.enqueue") as enqueue:
            runner.run()

        cfg.refresh_from_db()
        enqueue.assert_not_called()
        self.assertIsNone(cfg.next_sync_at)

    def test_scheduler_reanchors_missed_schedule_after_long_downtime(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=1, sync_missed_schedule_grace_minutes=360)
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device,
            snmp_version="2c",
            community="public",
            target_override="10.0.0.1",
        )
        run = SyncRun.objects.create(device=self.device, trigger="scheduled", mode="apply", status="ok")
        cfg.record_sync_result(run, update_schedule=False)
        now = timezone.now()
        cfg.next_sync_at = now - timedelta(days=1)
        cfg.save(update_fields=("next_sync_at",))
        runner = ScheduledSNMPSyncJob(SimpleNamespace(user=None))
        runner.logger = SimpleNamespace(info=lambda _msg: None)

        with patch("netbox_snmp_sync.jobs.SNMPSyncJob.enqueue") as enqueue:
            runner.run()

        cfg.refresh_from_db()
        enqueue.assert_not_called()
        self.assertGreater(cfg.next_sync_at, now)
        self.assertEqual(cfg.sync_state, "waiting")
        self.assertIn("Re-anchored missed SNMP sync schedule", cfg.last_sync_message)

    def test_scheduler_still_queues_recently_due_schedule(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=1, sync_missed_schedule_grace_minutes=360)
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device,
            snmp_version="2c",
            community="public",
            target_override="10.0.0.1",
        )
        run = SyncRun.objects.create(device=self.device, trigger="scheduled", mode="apply", status="ok")
        cfg.record_sync_result(run, update_schedule=False)
        cfg.next_sync_at = timezone.now() - timedelta(minutes=10)
        cfg.save(update_fields=("next_sync_at",))
        fake_job = SimpleNamespace(job_id="11111111-1111-1111-1111-111111111111")
        runner = ScheduledSNMPSyncJob(SimpleNamespace(user=None))
        runner.logger = SimpleNamespace(info=lambda _msg: None)

        with patch("netbox_snmp_sync.jobs.SNMPSyncJob.enqueue", return_value=fake_job) as enqueue:
            runner.run()

        cfg.refresh_from_db()
        enqueue.assert_called_once()
        self.assertEqual(str(cfg.sync_job_id), fake_job.job_id)

    def test_per_device_schedule_change_reanchors_next_sync(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=24)
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public",
            next_sync_at=timezone.now() + timedelta(hours=24),
        )

        before_save = timezone.now()
        cfg.sync_interval_hours = 8
        cfg.save()
        cfg.refresh_from_db()

        self.assertGreaterEqual(cfg.next_sync_at, before_save + timedelta(hours=8))
        self.assertLess(cfg.next_sync_at, before_save + timedelta(hours=8, minutes=1))

    def test_device_form_rejects_invalid_sync_hours(self):
        form = DeviceSNMPConfigForm(
            data={
                "device": self.device.pk,
                "enabled": True,
                "snmp_version": "2c",
                "port": 161,
                "community": "public",
                "timeout": 2.0,
                "retries": 1,
                "skip_loopback_ips": True,
                "sync_interval_hours": "",
                "sync_at_hours": "3,99",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("sync_at_hours", form.errors)

    def test_settings_form_reanchors_existing_configs(self):
        settings = SNMPSyncConfig.objects.create(sync_interval_hours=24)
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public",
            next_sync_at=timezone.now() + timedelta(hours=24),
        )

        form = SNMPSyncConfigForm(
            data={
                "sync_interval_hours": 8,
                "sync_at_hours": "",
                "sync_job_timeout_seconds": settings.sync_job_timeout_seconds,
                "sync_stale_job_marker_minutes": settings.sync_stale_job_marker_minutes,
                "sync_missed_schedule_grace_minutes": settings.sync_missed_schedule_grace_minutes,
                "update_existing": settings.update_existing,
                "set_mac_address": settings.set_mac_address,
                "write_vlans": settings.write_vlans,
                "create_vlans": settings.create_vlans,
                "vlan_subinterface_inference": settings.vlan_subinterface_inference,
                "history_keep_days": settings.history_keep_days,
                "history_keep_count": settings.history_keep_count,
            },
            instance=settings,
        )
        self.assertTrue(form.is_valid(), form.errors)

        before_save = timezone.now()
        form.save()
        cfg.refresh_from_db()

        self.assertGreaterEqual(cfg.next_sync_at, before_save + timedelta(hours=8))
        self.assertLess(cfg.next_sync_at, before_save + timedelta(hours=8, minutes=1))

    def test_settings_form_spreads_existing_configs(self):
        settings = SNMPSyncConfig.objects.create(sync_interval_hours=24)
        devices = [self.device]
        for index in range(2, 5):
            devices.append(Device.objects.create(
                name=f"sw{index}",
                device_type=self.device.device_type,
                role=self.device.role,
                site=self.device.site,
            ))
        for device in devices:
            DeviceSNMPConfig.objects.create(device=device, snmp_version="2c", community="public")

        form = SNMPSyncConfigForm(
            data={
                "sync_interval_hours": 8,
                "sync_at_hours": "",
                "sync_job_timeout_seconds": settings.sync_job_timeout_seconds,
                "sync_stale_job_marker_minutes": settings.sync_stale_job_marker_minutes,
                "sync_missed_schedule_grace_minutes": settings.sync_missed_schedule_grace_minutes,
                "update_existing": settings.update_existing,
                "set_mac_address": settings.set_mac_address,
                "write_vlans": settings.write_vlans,
                "create_vlans": settings.create_vlans,
                "vlan_subinterface_inference": settings.vlan_subinterface_inference,
                "history_keep_days": settings.history_keep_days,
                "history_keep_count": settings.history_keep_count,
            },
            instance=settings,
        )
        self.assertTrue(form.is_valid(), form.errors)

        form.save()
        next_times = list(DeviceSNMPConfig.objects.order_by("device__name").values_list("next_sync_at", flat=True))

        self.assertEqual(len(set(next_times)), len(next_times))
        self.assertLessEqual(max(next_times) - min(next_times), timedelta(minutes=15))

    def test_settings_form_does_not_reanchor_per_device_override(self):
        settings = SNMPSyncConfig.objects.create(sync_interval_hours=24)
        inherited_cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public",
            next_sync_at=timezone.now() + timedelta(hours=24),
        )
        override_device = Device.objects.create(
            name="sw2",
            device_type=self.device.device_type,
            role=self.device.role,
            site=self.device.site,
        )
        override_next = timezone.now() + timedelta(hours=3)
        override_cfg = DeviceSNMPConfig.objects.create(
            device=override_device, snmp_version="2c", community="public",
            sync_interval_hours=12, next_sync_at=override_next,
        )

        form = SNMPSyncConfigForm(
            data={
                "sync_interval_hours": 8,
                "sync_at_hours": "",
                "sync_job_timeout_seconds": settings.sync_job_timeout_seconds,
                "sync_stale_job_marker_minutes": settings.sync_stale_job_marker_minutes,
                "sync_missed_schedule_grace_minutes": settings.sync_missed_schedule_grace_minutes,
                "update_existing": settings.update_existing,
                "set_mac_address": settings.set_mac_address,
                "write_vlans": settings.write_vlans,
                "create_vlans": settings.create_vlans,
                "vlan_subinterface_inference": settings.vlan_subinterface_inference,
                "history_keep_days": settings.history_keep_days,
                "history_keep_count": settings.history_keep_count,
            },
            instance=settings,
        )
        self.assertTrue(form.is_valid(), form.errors)

        before_save = timezone.now()
        form.save()
        inherited_cfg.refresh_from_db()
        override_cfg.refresh_from_db()

        self.assertGreaterEqual(inherited_cfg.next_sync_at, before_save + timedelta(hours=8))
        self.assertEqual(override_cfg.next_sync_at, override_next)

    def test_global_reschedule_spreads_per_device_only_schedules(self):
        SNMPSyncConfig.objects.create(sync_interval_hours=0)
        devices = [self.device]
        for index in range(2, 5):
            devices.append(Device.objects.create(
                name=f"sw{index}",
                device_type=self.device.device_type,
                role=self.device.role,
                site=self.device.site,
            ))
        for device in devices:
            DeviceSNMPConfig.objects.create(
                device=device, snmp_version="2c", community="public", sync_interval_hours=8,
            )

        DeviceSNMPConfig.reset_all_next_sync(timezone.now())
        next_times = list(DeviceSNMPConfig.objects.order_by("device__name").values_list("next_sync_at", flat=True))

        self.assertEqual(len(set(next_times)), len(next_times))
        self.assertLessEqual(max(next_times) - min(next_times), timedelta(minutes=15))

    def test_is_sync_due_detects_past_next_sync(self):
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device, snmp_version="2c", community="public",
            next_sync_at=timezone.now() - timedelta(minutes=1),
        )

        self.assertTrue(cfg.is_sync_due)

    def test_sync_job_state_lifecycle(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        job_id = "11111111-1111-1111-1111-111111111111"

        cfg.mark_sync_queued(job_id)
        cfg.refresh_from_db()
        self.assertEqual(cfg.sync_state, "queued")
        self.assertTrue(cfg.has_active_sync_job())

        cfg.mark_sync_started(job_id)
        cfg.refresh_from_db()
        self.assertEqual(cfg.sync_state, "running")

        cfg.clear_sync_job()
        cfg.refresh_from_db()
        self.assertEqual(cfg.sync_state, "disabled")
        self.assertFalse(cfg.has_active_sync_job())

    def test_sync_job_slot_claim_is_atomic(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")

        claim_id = cfg.claim_sync_slot()
        competing_cfg = DeviceSNMPConfig.objects.get(pk=cfg.pk)

        self.assertIsNotNone(claim_id)
        self.assertIsNone(competing_cfg.claim_sync_slot())

        cfg.clear_sync_job()
        competing_cfg.refresh_from_db()

        self.assertIsNotNone(competing_cfg.claim_sync_slot())

    def test_mark_sync_queued_preserves_started_marker(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        job_id = "11111111-1111-1111-1111-111111111111"
        cfg.mark_sync_started(job_id)
        started_at = cfg.sync_started_at

        cfg.mark_sync_queued(job_id)
        cfg.refresh_from_db()

        self.assertEqual(cfg.sync_started_at, started_at)
        self.assertEqual(cfg.sync_state, "running")

    def test_mark_sync_started_does_not_overwrite_different_job_marker(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        active_job_id = "11111111-1111-1111-1111-111111111111"
        stale_job_id = "22222222-2222-2222-2222-222222222222"
        cfg.mark_sync_queued(active_job_id)

        self.assertFalse(cfg.mark_sync_started(stale_job_id))
        cfg.refresh_from_db()

        self.assertEqual(str(cfg.sync_job_id), active_job_id)
        self.assertIsNone(cfg.sync_started_at)
        self.assertEqual(cfg.sync_state, "queued")

    def test_clear_sync_job_does_not_clear_different_job_marker(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        active_job_id = "11111111-1111-1111-1111-111111111111"
        stale_job_id = "22222222-2222-2222-2222-222222222222"
        cfg.mark_sync_queued(active_job_id)

        self.assertFalse(cfg.clear_sync_job(job_id=stale_job_id))
        cfg.refresh_from_db()

        self.assertEqual(str(cfg.sync_job_id), active_job_id)
        self.assertEqual(cfg.sync_state, "queued")

    def test_stale_sync_job_marker_is_cleared(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        cfg.mark_sync_queued(
            "11111111-1111-1111-1111-111111111111",
            reference=timezone.now() - timedelta(hours=3),
        )

        self.assertFalse(cfg.has_active_sync_job())
        cfg.refresh_from_db()
        self.assertIsNone(cfg.sync_job_id)

    def test_recent_sync_job_marker_is_kept_when_netbox_job_is_active(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        job_id = "11111111-1111-1111-1111-111111111111"
        Job.objects.create(name="SNMP Sync", job_id=job_id, status="running")
        cfg.mark_sync_started(job_id, reference=timezone.now() - timedelta(minutes=10))

        self.assertTrue(cfg.has_active_sync_job())
        cfg.refresh_from_db()
        self.assertEqual(str(cfg.sync_job_id), job_id)
        self.assertEqual(cfg.sync_state, "running")

    def test_old_active_sync_job_marker_is_cleared_after_worker_restart(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        job_id = "11111111-1111-1111-1111-111111111111"
        Job.objects.create(name="SNMP Sync", job_id=job_id, status="running")
        cfg.mark_sync_started(job_id, reference=timezone.now() - timedelta(hours=3))

        self.assertFalse(cfg.has_active_sync_job())
        cfg.refresh_from_db()
        self.assertIsNone(cfg.sync_job_id)
        self.assertEqual(cfg.sync_state, "disabled")
        self.assertIn("Cleared stale SNMP sync marker", cfg.last_sync_message)

    def test_stale_sync_job_marker_uses_configured_timeout(self):
        SNMPSyncConfig.objects.create(sync_stale_job_marker_minutes=30)
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        job_id = "11111111-1111-1111-1111-111111111111"
        Job.objects.create(name="SNMP Sync", job_id=job_id, status="running")
        cfg.mark_sync_started(job_id, reference=timezone.now() - timedelta(minutes=45))

        self.assertFalse(cfg.has_active_sync_job())
        cfg.refresh_from_db()
        self.assertIsNone(cfg.sync_job_id)

    def test_sync_job_marker_is_cleared_when_netbox_job_is_finished(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        job_id = "11111111-1111-1111-1111-111111111111"
        Job.objects.create(name="SNMP Sync", job_id=job_id, status="completed")
        cfg.mark_sync_queued(job_id, reference=timezone.now())

        self.assertFalse(cfg.has_active_sync_job())
        cfg.refresh_from_db()
        self.assertIsNone(cfg.sync_job_id)

    def test_recent_missing_sync_job_marker_is_kept_until_timeout(self):
        cfg = DeviceSNMPConfig.objects.create(device=self.device, snmp_version="2c", community="public")
        job_id = "11111111-1111-1111-1111-111111111111"
        cfg.mark_sync_queued(job_id, reference=timezone.now() - timedelta(minutes=10))

        self.assertTrue(cfg.has_active_sync_job())
        cfg.refresh_from_db()
        self.assertEqual(str(cfg.sync_job_id), job_id)

    def test_snmp_test_uses_sysname_probe_only(self):
        cfg = DeviceSNMPConfig.objects.create(
            device=self.device,
            snmp_version="2c",
            community="public",
            target_override="10.0.0.1",
        )

        with patch("netbox_snmp_sync.views._quick_sys_name_blocking", return_value=("sw1-snmp", None)) as quick:
            with patch("netbox_snmp_sync.views._collect_blocking") as collect:
                ok, message = views._evaluate(cfg.to_spec())

        self.assertTrue(ok)
        self.assertIn("sysName=sw1-snmp", message)
        quick.assert_called_once()
        collect.assert_not_called()

    def _create_sync_run(self, *, created, message):
        run = SyncRun.objects.create(
            device=self.device,
            trigger="manual",
            mode="apply",
            status="ok",
            message=message,
        )
        SyncRun.objects.filter(pk=run.pk).update(created=created)
        run.refresh_from_db()
        return run

    def _run_prune_job(self):
        runner = PruneSyncRunsJob(SimpleNamespace(user=None))
        runner.logger = SimpleNamespace(info=lambda _msg: None)
        runner.run()

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    def test_prune_sync_runs_respects_keep_count(self):
        SNMPSyncConfig.objects.create(history_keep_days=0, history_keep_count=3)
        now = timezone.now()
        for index in range(5):
            self._create_sync_run(created=now - timedelta(minutes=index), message=f"run-{index}")

        self._run_prune_job()

        remaining = set(SyncRun.objects.values_list("message", flat=True))
        self.assertEqual(SyncRun.objects.count(), 3)
        self.assertEqual(remaining, {"run-0", "run-1", "run-2"})

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    def test_prune_sync_runs_respects_keep_days(self):
        SNMPSyncConfig.objects.create(history_keep_days=10, history_keep_count=0)
        now = timezone.now()
        self._create_sync_run(created=now - timedelta(days=1), message="recent")
        self._create_sync_run(created=now - timedelta(days=11), message="old")
        self._create_sync_run(created=now - timedelta(days=30), message="older")

        self._run_prune_job()

        remaining = set(SyncRun.objects.values_list("message", flat=True))
        self.assertEqual(remaining, {"recent"})

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    def test_prune_sync_runs_applies_days_before_count(self):
        SNMPSyncConfig.objects.create(history_keep_days=10, history_keep_count=2)
        now = timezone.now()
        self._create_sync_run(created=now - timedelta(days=1), message="newest")
        self._create_sync_run(created=now - timedelta(days=2), message="second")
        self._create_sync_run(created=now - timedelta(days=3), message="third")
        self._create_sync_run(created=now - timedelta(days=12), message="old")

        self._run_prune_job()

        remaining = set(SyncRun.objects.values_list("message", flat=True))
        self.assertEqual(SyncRun.objects.count(), 2)
        self.assertEqual(remaining, {"newest", "second"})

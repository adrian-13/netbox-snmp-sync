import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import Job
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from ipam.models import VLAN, IPAddress

from netbox_snmp_sync import engine
from netbox_snmp_sync.dto import DeviceData, InterfaceData, IPAddressData, VlanData
from netbox_snmp_sync.forms import DeviceSNMPConfigForm, SNMPSyncConfigForm
from netbox_snmp_sync.jobs import SYSTEM_USERNAME, ScheduledSNMPSyncJob, _collect_with_job_timeout, _fake_request
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

    def test_job_collection_timeout_raises_clear_error(self):
        async def slow_collect(_spec):
            await asyncio.sleep(1)

        with patch("netbox_snmp_sync.jobs.collect_with_ping", side_effect=slow_collect):
            with self.assertRaisesRegex(TimeoutError, "timed out after 0.01 seconds"):
                asyncio.run(_collect_with_job_timeout(object(), 0.01))

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
                "update_existing": settings.update_existing,
                "set_mac_address": settings.set_mac_address,
                "write_vlans": settings.write_vlans,
                "create_vlans": settings.create_vlans,
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
                "update_existing": settings.update_existing,
                "set_mac_address": settings.set_mac_address,
                "write_vlans": settings.write_vlans,
                "create_vlans": settings.create_vlans,
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
                "update_existing": settings.update_existing,
                "set_mac_address": settings.set_mac_address,
                "write_vlans": settings.write_vlans,
                "create_vlans": settings.create_vlans,
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

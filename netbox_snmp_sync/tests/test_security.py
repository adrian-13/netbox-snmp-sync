"""Security-focused tests: SNMP secrets must not leak via API/UI, and the custom
action views (which trigger SNMP polls / writes) must require authentication + permission."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from rest_framework.test import APIClient
from users.models import ObjectPermission

from netbox_snmp_sync.tables import SNMP_TEST_BUTTON
from netbox_snmp_sync.models import DeviceSNMPConfig, SNMPSyncConfig

User = get_user_model()

SECRET_COMM = "S3CRET_COMMUNITY"
SECRET_AUTH = "S3CRET_AUTHKEY"
SECRET_PRIV = "S3CRET_PRIVKEY"


class DeviceSNMPConfigListViewTestCase(TestCase):
    def test_empty_list_view_renders(self):
        admin = User.objects.create_superuser(username="empty_admin", email="empty@example.com", password="x")
        client = Client()
        client.force_login(admin)

        response = client.get(reverse("plugins:netbox_snmp_sync:devicesnmpconfig_list"))

        self.assertEqual(response.status_code, 200)


class SecurityTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Lab", slug="lab")
        mf = Manufacturer.objects.create(name="MikroTik", slug="mikrotik")
        dt = DeviceType.objects.create(manufacturer=mf, model="CRS", slug="crs")
        role = DeviceRole.objects.create(name="Switch", slug="switch")
        cls.device = Device.objects.create(name="sw1", device_type=dt, role=role, site=site)
        cls.cfg = DeviceSNMPConfig.objects.create(
            device=cls.device, snmp_version="3", community=SECRET_COMM,
            username="snmpuser", auth_key=SECRET_AUTH, priv_key=SECRET_PRIV,
        )
        cls.admin = User.objects.create_superuser(username="sec_admin", email="a@b.c", password="x")
        cls.bob = User.objects.create_user(username="sec_bob", password="x")  # no permissions
        cls.viewer = User.objects.create_user(username="sec_viewer", password="x")
        cls.operator = User.objects.create_user(username="sec_operator", password="x")
        content_type = ContentType.objects.get_for_model(DeviceSNMPConfig)
        view_permission = ObjectPermission.objects.create(name="Can view SNMP configs", actions=["view"])
        view_permission.object_types.add(content_type)
        view_permission.users.add(cls.viewer, cls.operator)
        change_permission = ObjectPermission.objects.create(name="Can change SNMP configs", actions=["change"])
        change_permission.object_types.add(content_type)
        change_permission.users.add(cls.operator)

    # --- REST API ---

    def test_api_never_returns_snmp_secrets(self):
        c = APIClient()
        c.force_authenticate(user=self.admin)
        r = c.get(reverse("plugins-api:netbox_snmp_sync-api:devicesnmpconfig-detail", args=[self.cfg.pk]))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        for field in ("community", "auth_key", "priv_key"):
            self.assertNotIn(field, data, f"{field} must be write-only (not returned)")
        body = r.content.decode()
        for secret in (SECRET_COMM, SECRET_AUTH, SECRET_PRIV):
            self.assertNotIn(secret, body, "secret value leaked in API response")

    def test_api_denies_user_without_permission(self):
        c = APIClient()
        c.force_authenticate(user=self.bob)  # authenticated but no object permissions
        r = c.get(reverse("plugins-api:netbox_snmp_sync-api:devicesnmpconfig-list"))
        self.assertEqual(r.status_code, 403)

    def test_api_cannot_write_scheduler_state(self):
        c = APIClient()
        c.force_authenticate(user=self.admin)
        url = reverse("plugins-api:netbox_snmp_sync-api:devicesnmpconfig-detail", args=[self.cfg.pk])
        attempted_next_sync = timezone.now().isoformat()

        r = c.patch(
            url,
            {
                "next_sync_at": attempted_next_sync,
                "last_sync_status": "ok",
                "consecutive_sync_failures": 9,
                "sync_job_id": "11111111-1111-1111-1111-111111111111",
            },
            format="json",
        )

        self.assertEqual(r.status_code, 200)
        self.cfg.refresh_from_db()
        self.assertIsNone(self.cfg.next_sync_at)
        self.assertEqual(self.cfg.last_sync_status, "")
        self.assertEqual(self.cfg.consecutive_sync_failures, 0)
        self.assertIsNone(self.cfg.sync_job_id)

    def test_api_requires_authentication(self):
        r = APIClient().get(reverse("plugins-api:netbox_snmp_sync-api:devicesnmpconfig-list"))
        self.assertIn(r.status_code, (401, 403))

    # --- Custom action views (SNMP poll / writes) ---

    def test_action_views_require_login(self):
        anon = Client()
        for name, args in [
            ("devicesnmpconfig_test", [self.cfg.pk]),
            ("devicesnmpconfig_preview", [self.cfg.pk]),
            ("devicesnmpconfig_sync", [self.cfg.pk]),
            ("devicesnmpconfig_reset_schedule", [self.cfg.pk]),
            ("devicesnmpconfig_reconcile_marker", [self.cfg.pk]),
            ("bulk_setup", []),
        ]:
            url = reverse(f"plugins:netbox_snmp_sync:{name}", args=args)
            r = anon.get(url)
            self.assertEqual(r.status_code, 302, f"{url} must redirect anonymous users to login")
            self.assertIn("/login", r.url, f"{url} should redirect to login")

    def test_preview_not_executed_without_permission(self):
        # Logged in but no permissions → preview must NOT render (no SNMP poll happens)
        c = Client()
        c.force_login(self.bob)
        r = c.get(reverse("plugins:netbox_snmp_sync:devicesnmpconfig_preview", args=[self.cfg.pk]))
        self.assertEqual(r.status_code, 302)  # redirected away, not rendered

    def test_sync_action_does_not_accept_get(self):
        c = Client()
        c.force_login(self.admin)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_sync", args=[self.cfg.pk])

        with patch("netbox_snmp_sync.views.SNMPSyncJob.enqueue") as enqueue:
            r = c.get(url, {"mode": "apply"})

        self.assertEqual(r.status_code, 405)
        enqueue.assert_not_called()

    def test_sync_action_requires_change_permission(self):
        c = Client()
        c.force_login(self.viewer)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_sync", args=[self.cfg.pk])

        with patch("netbox_snmp_sync.views.SNMPSyncJob.enqueue") as enqueue:
            r = c.post(url, {"mode": "apply"})

        self.assertEqual(r.status_code, 302)
        enqueue.assert_not_called()

    def test_sync_action_allows_change_permission(self):
        c = Client()
        c.force_login(self.operator)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_sync", args=[self.cfg.pk])
        fake_job = SimpleNamespace(
            job_id="11111111-1111-1111-1111-111111111111",
            get_absolute_url=lambda: "/jobs/1/",
        )

        with patch("netbox_snmp_sync.views.SNMPSyncJob.enqueue", return_value=fake_job) as enqueue:
            r = c.post(url, {"mode": "apply"})

        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, "/jobs/1/")
        enqueue.assert_called_once()

    def test_single_test_action_does_not_accept_get(self):
        c = Client()
        c.force_login(self.admin)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_test", args=[self.cfg.pk])

        with patch("netbox_snmp_sync.views._run_test") as run_test:
            r = c.get(url)

        self.assertEqual(r.status_code, 405)
        run_test.assert_not_called()

    def test_single_test_action_requires_change_permission(self):
        c = Client()
        c.force_login(self.viewer)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_test", args=[self.cfg.pk])

        with patch("netbox_snmp_sync.views._run_test") as run_test:
            r = c.post(url)

        self.assertEqual(r.status_code, 302)
        run_test.assert_not_called()

    def test_single_test_action_allows_change_permission(self):
        c = Client()
        c.force_login(self.operator)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_test", args=[self.cfg.pk])

        with patch(
            "netbox_snmp_sync.views._run_test",
            return_value={"device": self.device, "target": "10.0.0.1", "ok": True, "message": "ok"},
        ) as run_test:
            r = c.post(url)

        self.assertEqual(r.status_code, 200)
        run_test.assert_called_once()

    def test_table_test_action_uses_parent_bulk_form(self):
        self.assertNotIn("<form", SNMP_TEST_BUTTON)
        self.assertIn("formaction=", SNMP_TEST_BUTTON)
        self.assertIn("formmethod=\"post\"", SNMP_TEST_BUTTON)

    def test_bulk_test_action_requires_change_permission(self):
        c = Client()
        c.force_login(self.viewer)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_bulk_test")

        with patch("netbox_snmp_sync.views._evaluate") as evaluate:
            r = c.post(url, {"pk": [self.cfg.pk]})

        self.assertEqual(r.status_code, 302)
        evaluate.assert_not_called()

    def test_reset_schedule_action_does_not_accept_get(self):
        c = Client()
        c.force_login(self.admin)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_reset_schedule", args=[self.cfg.pk])

        r = c.get(url)

        self.assertEqual(r.status_code, 405)

    def test_reset_schedule_action_requires_change_permission(self):
        c = Client()
        c.force_login(self.viewer)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_reset_schedule", args=[self.cfg.pk])
        original_next = self.cfg.next_sync_at

        r = c.post(url)

        self.assertEqual(r.status_code, 302)
        self.cfg.refresh_from_db()
        self.assertEqual(self.cfg.next_sync_at, original_next)

    def test_reset_schedule_action_allows_change_permission(self):
        settings = SNMPSyncConfig.get()
        settings.sync_interval_hours = 8
        settings.sync_at_hours = ""
        settings.save()
        c = Client()
        c.force_login(self.operator)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_reset_schedule", args=[self.cfg.pk])

        r = c.post(url)

        self.assertEqual(r.status_code, 302)
        self.cfg.refresh_from_db()
        self.assertIsNotNone(self.cfg.next_sync_at)

    def test_reconcile_marker_action_does_not_accept_get(self):
        c = Client()
        c.force_login(self.admin)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_reconcile_marker", args=[self.cfg.pk])

        r = c.get(url)

        self.assertEqual(r.status_code, 405)

    def test_reconcile_marker_action_requires_change_permission(self):
        self.cfg.mark_sync_queued(
            "11111111-1111-1111-1111-111111111111",
            reference=timezone.now() - timedelta(hours=3),
        )
        c = Client()
        c.force_login(self.viewer)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_reconcile_marker", args=[self.cfg.pk])

        r = c.post(url)

        self.assertEqual(r.status_code, 302)
        self.cfg.refresh_from_db()
        self.assertIsNotNone(self.cfg.sync_job_id)

    def test_reconcile_marker_action_clears_stale_marker(self):
        self.cfg.mark_sync_queued(
            "11111111-1111-1111-1111-111111111111",
            reference=timezone.now() - timedelta(hours=3),
        )
        c = Client()
        c.force_login(self.operator)
        url = reverse("plugins:netbox_snmp_sync:devicesnmpconfig_reconcile_marker", args=[self.cfg.pk])

        r = c.post(url)

        self.assertEqual(r.status_code, 302)
        self.cfg.refresh_from_db()
        self.assertIsNone(self.cfg.sync_job_id)

    # --- UI detail must not render v3 keys ---

    def test_v3_keys_not_in_detail_page(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get(self.cfg.get_absolute_url())
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertNotIn(SECRET_AUTH, body, "SNMPv3 auth key must never render in the UI")
        self.assertNotIn(SECRET_PRIV, body, "SNMPv3 priv key must never render in the UI")

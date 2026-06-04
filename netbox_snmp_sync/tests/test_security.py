"""Security-focused tests: SNMP secrets must not leak via API/UI, and the custom
action views (which trigger SNMP polls / writes) must require authentication + permission."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from rest_framework.test import APIClient

from netbox_snmp_sync.models import DeviceSNMPConfig

User = get_user_model()

SECRET_COMM = "S3CRET_COMMUNITY"
SECRET_AUTH = "S3CRET_AUTHKEY"
SECRET_PRIV = "S3CRET_PRIVKEY"


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

    def test_api_requires_authentication(self):
        r = APIClient().get(reverse("plugins-api:netbox_snmp_sync-api:devicesnmpconfig-list"))
        self.assertIn(r.status_code, (401, 403))

    # --- Custom action views (SNMP poll / writes) ---

    def test_action_views_require_login(self):
        anon = Client()
        for name, args in [
            ("devicesnmpconfig_preview", [self.cfg.pk]),
            ("devicesnmpconfig_sync", [self.cfg.pk]),
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

    # --- UI detail must not render v3 keys ---

    def test_v3_keys_not_in_detail_page(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get(self.cfg.get_absolute_url())
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertNotIn(SECRET_AUTH, body, "SNMPv3 auth key must never render in the UI")
        self.assertNotIn(SECRET_PRIV, body, "SNMPv3 priv key must never render in the UI")

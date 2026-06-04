from django.urls import include, path

from utilities.urls import get_model_urls

from . import views  # noqa: F401 — importing runs the @register_model_view decorators

urlpatterns = [
    path(
        "device-snmp-configs/",
        include(get_model_urls("netbox_snmp_sync", "devicesnmpconfig", detail=False)),
    ),
    path(
        "device-snmp-configs/<int:pk>/",
        include(get_model_urls("netbox_snmp_sync", "devicesnmpconfig")),
    ),
    path(
        "sync-runs/",
        include(get_model_urls("netbox_snmp_sync", "syncrun", detail=False)),
    ),
    path(
        "sync-runs/<int:pk>/",
        include(get_model_urls("netbox_snmp_sync", "syncrun")),
    ),
    path("bulk-setup/", views.BulkSNMPConfigView.as_view(), name="bulk_setup"),
    path("settings/", views.SNMPSyncSettingsView.as_view(), name="settings"),
]

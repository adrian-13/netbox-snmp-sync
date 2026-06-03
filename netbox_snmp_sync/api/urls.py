from netbox.api.routers import NetBoxRouter

from .views import DeviceSNMPConfigViewSet, SyncRunViewSet

router = NetBoxRouter()
router.register("device-snmp-configs", DeviceSNMPConfigViewSet)
router.register("sync-runs", SyncRunViewSet)

urlpatterns = router.urls

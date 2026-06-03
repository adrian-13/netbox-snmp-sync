from netbox.api.viewsets import NetBoxModelViewSet

from .. import filtersets
from ..models import DeviceSNMPConfig, SyncRun
from .serializers import DeviceSNMPConfigSerializer, SyncRunSerializer


class DeviceSNMPConfigViewSet(NetBoxModelViewSet):
    queryset = DeviceSNMPConfig.objects.all()
    serializer_class = DeviceSNMPConfigSerializer
    filterset_class = filtersets.DeviceSNMPConfigFilterSet


class SyncRunViewSet(NetBoxModelViewSet):
    queryset = SyncRun.objects.all()
    serializer_class = SyncRunSerializer
    filterset_class = filtersets.SyncRunFilterSet

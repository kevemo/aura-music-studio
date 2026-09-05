from __future__ import annotations

from .shared_sky_destination_adapters import CapabilityState
from .shared_sky_transport_models import (
    BroadcastState, DestinationState, OperationInProgress, PreflightBlocked,
    TransportRateLimited,
)
from .shared_sky_transport_operations import TransportOperations
from .shared_sky_transport_persistence import TransportPersistence
from .shared_sky_transport_support import TransportSupport


class SharedSkyTransportStore(TransportOperations, TransportSupport, TransportPersistence):
    pass


transport = SharedSkyTransportStore()

__all__ = [
    "BroadcastState", "CapabilityState", "DestinationState", "OperationInProgress",
    "PreflightBlocked", "TransportRateLimited", "SharedSkyTransportStore", "transport",
]

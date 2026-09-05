from __future__ import annotations

from .shared_sky_destination_adapters import CapabilityState
from .shared_sky_transport_extensions import TransportExtensionsMixin
from .shared_sky_transport_models import (
    BroadcastState,
    DestinationState,
    OperationInProgress,
    PreflightBlocked,
    TransportRateLimited,
)
from .shared_sky_transport_operations import TransportOperations
from .shared_sky_transport_persistence import TransportPersistence
from .shared_sky_transport_recovery import TransportRecoveryMixin
from .shared_sky_transport_support import TransportSupport


class SharedSkyTransportStore(
    TransportRecoveryMixin,
    TransportExtensionsMixin,
    TransportOperations,
    TransportSupport,
    TransportPersistence,
):
    """Canonical Shared Sky transport control-plane service.

    Recovery and compatibility mixins sit ahead of the core operations layer so narrowly
    scoped hardening can override stop/preflight/provider-start behaviour while continuing
    to delegate durable base initialization through cooperative ``super()``.
    """


transport = SharedSkyTransportStore()

__all__ = [
    "BroadcastState",
    "CapabilityState",
    "DestinationState",
    "OperationInProgress",
    "PreflightBlocked",
    "TransportRateLimited",
    "SharedSkyTransportStore",
    "transport",
]

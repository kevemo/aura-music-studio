from __future__ import annotations

from .shared_sky_destination_adapters import CapabilityState
from .shared_sky_transport_browser_playback import TransportBrowserPlaybackMixin
from .shared_sky_transport_extensions import TransportExtensionsMixin
from .shared_sky_transport_local_recording import TransportLocalRecordingMixin
from .shared_sky_transport_media import TransportMediaMixin
from .shared_sky_transport_media_lifecycle import TransportMediaLifecycleMixin
from .shared_sky_transport_media_privacy import TransportMediaPrivacyMixin
from .shared_sky_transport_media_readiness import TransportMediaStartupReadinessMixin
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
    TransportMediaPrivacyMixin,
    TransportBrowserPlaybackMixin,
    TransportMediaStartupReadinessMixin,
    TransportMediaLifecycleMixin,
    TransportLocalRecordingMixin,
    TransportMediaMixin,
    TransportRecoveryMixin,
    TransportExtensionsMixin,
    TransportOperations,
    TransportSupport,
    TransportPersistence,
):
    """Canonical Shared Sky transport control-plane and first-party media service.

    The privacy boundary is intentionally first in the cooperative MRO so member-facing
    status responses cannot expose local filesystem roots added by lower media-runtime
    layers. Browser playback decorates the signed descriptor with the secure cookie exchange,
    and startup readiness requires actual viewer-playable HLS evidence before the internal
    path can count toward LIVE. Lower lifecycle/recovery/provider layers retain ownership of
    durable session and destination state.
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

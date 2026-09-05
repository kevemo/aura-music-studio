from __future__ import annotations

import os

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

    def participant_capacity(self, live_session_id: str) -> int:
        """Return the measured/configured multi-host admission ceiling for one broadcast.

        This is an admission contract only; it does not claim that WebRTC/SFU guest media is
        deployed. Capacity remains fail-closed unless deployment explicitly configures a bounded
        value and the referenced canonical transport session exists in a non-terminal state.
        The product ceiling is eight total participants, including the host.
        """

        broadcast_id = str(live_session_id or "").strip()
        if not broadcast_id:
            return 0
        try:
            configured = int(os.getenv("SHARED_SKY_MULTIHOST_MAX_PARTICIPANTS", "0") or 0)
        except ValueError:
            return 0
        configured = max(0, min(configured, 8))
        if configured < 1:
            return 0
        try:
            with self.connect() as con:
                row = con.execute(
                    "SELECT state FROM shared_sky_transport_sessions WHERE broadcast_id=? LIMIT 1",
                    (broadcast_id,),
                ).fetchone()
        except Exception:
            return 0
        if not row:
            return 0
        state = str(row["state"] or "").strip().lower()
        if state in {"ended", "failed", "cancelled"}:
            return 0
        return configured


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

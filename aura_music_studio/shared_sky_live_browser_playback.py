from __future__ import annotations

import importlib
from typing import Any

from . import shared_sky_live_community as live
from .shared_sky_live_integrations import Chat2PlaybackAdapter, _INTEGRATION_STATUS


class BrowserSafeChat2PlaybackAdapter(Chat2PlaybackAdapter):
    """Prevent a server-valid descriptor being misrepresented as browser-playable.

    Chat 2 currently returns a Bearer authorization value separately from the HLS manifest URL.
    The current Shared Sky Watch runtime uses the native HTML video element and has no packaged
    header-capable HLS loader. Native video requests cannot attach that custom Authorization header.

    Until Chat 2 supplies a browser credential mode (or Chat 4 deliberately packages a vetted HLS
    client with header/CORS support), the viewer network fails closed instead of exposing a manifest
    that the browser cannot lawfully/technically request.
    """

    def descriptor(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        descriptor = dict(super().descriptor(broadcast_id, viewer_user_id))
        if not descriptor.get("available"):
            return descriptor
        authorization = descriptor.get("authorization")
        if not isinstance(authorization, dict):
            descriptor["browser_authorization_mode"] = "none"
            return descriptor
        scheme = str(authorization.get("scheme") or "").strip().lower()
        if scheme != "bearer":
            descriptor["browser_authorization_mode"] = scheme or "unknown"
            return descriptor
        descriptor.update(
            {
                "available": False,
                "state": "unavailable",
                "reason": "browser_bearer_playback_runtime_pending",
                "manifest_url": None,
                "authorization": None,
                "token_expires_at": None,
                "browser_authorization_mode": "bearer_header_requires_hls_runtime",
                "browser_playback_requirement": (
                    "Chat 2 browser credential exchange or packaged header-capable HLS client"
                ),
            }
        )
        return descriptor


def harden_browser_playback_integration() -> dict[str, Any]:
    """Replace the generic Chat 2 adapter with the browser-safe variant when Chat 2 is present."""

    status = _INTEGRATION_STATUS.get("chat2_playback") or {}
    if status.get("state") != "registered":
        return dict(status)
    try:
        transport_module = importlib.import_module(f"{__package__}.shared_sky_transport_domain")
        transport_store = getattr(transport_module, "transport")
        live.register_playback_adapter(BrowserSafeChat2PlaybackAdapter(transport_store, live.community))
        updated = {
            "state": "registered",
            "source": "aura_music_studio.shared_sky_transport_domain.transport",
            "browser_runtime": "native_video_fail_closed_for_bearer_header",
        }
        _INTEGRATION_STATUS["chat2_playback"] = updated
        return dict(updated)
    except Exception as exc:
        degraded = {
            "state": "degraded",
            "reason": str(getattr(exc, "code", "chat2_browser_adapter_registration_failed"))[:120],
        }
        _INTEGRATION_STATUS["chat2_playback"] = degraded
        live.register_playback_adapter(live.UnavailablePlaybackAdapter())
        return dict(degraded)


__all__ = ["BrowserSafeChat2PlaybackAdapter", "harden_browser_playback_integration"]

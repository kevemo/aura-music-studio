from __future__ import annotations

from typing import Any

from . import access_control
from . import shared_sky_live_community as live
from .shared_sky_live_integrations import Chat2PlaybackAdapter, _INTEGRATION_STATUS
from .shared_sky_transport_domain import transport


class Chat2CookieBootstrapPlaybackAdapter(Chat2PlaybackAdapter):
    """Expose Chat 2 first-party HLS to native video without leaking bearer credentials.

    The browser receives only a same-origin bootstrap URL. The bootstrap route performs the
    Chat 4 visibility/access decision server-side, mints Chat 2's short-lived playback bearer,
    stores it in an HttpOnly broadcast-scoped cookie and redirects to the actual manifest.
    """

    def descriptor(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        descriptor = dict(super().descriptor(broadcast_id, viewer_user_id))
        if not descriptor.get("available"):
            return descriptor
        authorization = descriptor.get("authorization")
        if not isinstance(authorization, dict):
            descriptor["browser_authorization_mode"] = "none"
            return descriptor
        if str(authorization.get("scheme") or "").strip().lower() != "bearer":
            descriptor["browser_authorization_mode"] = str(
                authorization.get("scheme") or "unknown"
            ).strip().lower()
            return descriptor

        try:
            owner_user_id = self._owner(broadcast_id)
            status = self.transport.status(owner_user_id, broadcast_id)
            raw = dict(status.get("playback") or {}) if isinstance(status, dict) else {}
            browser = dict(raw.get("browser_authorization") or {})
        except Exception:
            browser = {}
        if str(browser.get("mode") or "") != "cookie_exchange":
            descriptor.update(
                {
                    "available": False,
                    "state": "unavailable",
                    "reason": "browser_playback_exchange_unavailable",
                    "manifest_url": None,
                    "authorization": None,
                    "token_expires_at": None,
                    "browser_authorization_mode": "unavailable",
                }
            )
            return descriptor

        descriptor.update(
            {
                "available": True,
                "state": "ready" if descriptor.get("transport_state") == "live" else descriptor.get("state"),
                "manifest_url": f"/shared-sky/media/{broadcast_id}/bootstrap",
                "authorization": None,
                "token_expires_at": None,
                "browser_authorization_mode": "cookie_bootstrap_redirect",
                "browser_playback_requirement": None,
                "source": "chat2_transport_browser_bridge",
            }
        )
        return descriptor


def install_chat2_browser_playback_bridge() -> dict[str, Any]:
    """Install the Chat 2 browser bridge after Chat 4's generic fail-closed adapter."""
    status = dict(_INTEGRATION_STATUS.get("chat2_playback") or {})
    if status.get("state") != "registered":
        return status
    try:
        live.register_playback_adapter(Chat2CookieBootstrapPlaybackAdapter(transport, live.community))
        prefixes = tuple(access_control.PUBLIC_PREFIXES)
        if "/shared-sky/media/" not in prefixes:
            access_control.PUBLIC_PREFIXES = prefixes + ("/shared-sky/media/",)
        updated = {
            **status,
            "state": "registered",
            "browser_runtime": "chat2_cookie_bootstrap_redirect",
            "browser_token_in_url": False,
            "media_public_prefix_registered": True,
        }
        _INTEGRATION_STATUS["chat2_playback"] = updated
        return dict(updated)
    except Exception as exc:
        degraded = {
            **status,
            "state": "degraded",
            "reason": str(getattr(exc, "code", "chat2_browser_bridge_registration_failed"))[:120],
        }
        _INTEGRATION_STATUS["chat2_playback"] = degraded
        return degraded


__all__ = ["Chat2CookieBootstrapPlaybackAdapter", "install_chat2_browser_playback_bridge"]

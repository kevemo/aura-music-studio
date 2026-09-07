from __future__ import annotations


class TransportBrowserPlaybackMixin:
    """Describe the secure native-browser credential exchange for first-party HLS.

    The canonical bearer token remains out of URLs. A browser client can exchange that bearer
    once for an HttpOnly, broadcast-path-scoped cookie and then let native media requests send
    the cookie automatically to the same-origin Shared Sky media route.
    """

    def playback(self, user_id: str, broadcast_id: str, ttl: int = 120) -> dict:
        payload = super().playback(user_id, broadcast_id, ttl=ttl)
        capability = getattr(payload.get("capability_state"), "value", payload.get("capability_state"))
        if str(capability or "").lower() == "ready":
            payload["browser_authorization"] = {
                "mode": "cookie_exchange",
                "exchange_url": f"/shared-sky/media/{broadcast_id}/authorize",
                "method": "POST",
                "credential_source": "authorization_bearer_once",
                "cookie_http_only": True,
                "cookie_path_scoped": True,
                "token_in_manifest_url": False,
            }
        return payload


__all__ = ["TransportBrowserPlaybackMixin"]

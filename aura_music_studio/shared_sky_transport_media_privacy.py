from __future__ import annotations


class TransportMediaPrivacyMixin:
    """Final public-response boundary for first-party media diagnostics."""

    def status(self, user_id: str, broadcast_id: str) -> dict:
        payload = super().status(user_id, broadcast_id)
        media = dict(payload.get("internal_media") or {})
        health = dict(media.get("health") or {})
        media["health"] = {
            "enabled": bool(health.get("enabled")),
            "configured": bool(health.get("configured")),
            "ffmpeg_available": bool(health.get("ffmpeg_available")),
            "ffprobe_available": bool(health.get("ffprobe_available")),
            "recording_root_configured": bool(health.get("recording_root_configured")),
            "active_jobs": int(health.get("active_jobs") or 0),
            "runtime_mode": str(health.get("runtime_mode") or "unknown")[:80],
            "media_root_exposed": False,
        }
        payload["internal_media"] = media
        return payload


__all__ = ["TransportMediaPrivacyMixin"]

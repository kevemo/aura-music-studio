from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse

from .owner_identity import owner_session_authorized
from .shared_sky_internal_media import SharedSkyInternalMediaError, internal_media
from .shared_sky_transport_domain import transport


router = APIRouter(tags=["Shared Sky Internal Media"])


def _bearer(value: str) -> str:
    clean = (value or "").strip()
    if not clean.lower().startswith("bearer "):
        raise HTTPException(401, "Shared Sky playback authorization is required")
    token = clean[7:].strip()
    if not token or len(token) > 2048:
        raise HTTPException(401, "Shared Sky playback authorization is invalid")
    return token


def _owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")


@router.get(
    "/shared-sky/media/{broadcast_id}/{asset_path:path}",
    include_in_schema=False,
)
def shared_sky_media_asset(
    broadcast_id: str,
    asset_path: str,
    authorization: str = Header(default="", alias="Authorization"),
):
    token = _bearer(authorization)
    try:
        transport.verify_playback_token(token, expected_broadcast_id=broadcast_id)
        target = internal_media.playback_asset(broadcast_id, asset_path)
    except (ValueError, RuntimeError, SharedSkyInternalMediaError) as exc:
        raise HTTPException(401, "Shared Sky playback authorization is invalid") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Shared Sky playback asset is not available")
    suffix = target.suffix.lower()
    media_type = {
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/mp2t",
        ".m4s": "video/iso.segment",
    }.get(suffix, "application/octet-stream")
    headers = {
        "Cache-Control": "private, no-store" if suffix == ".m3u8" else "private, max-age=30",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    return FileResponse(target, media_type=media_type, headers=headers)


@router.get("/owner/shared-sky/api/internal-media/status")
def owner_internal_media_status(request: Request):
    _owner(request)
    health = internal_media.health()
    return {
        "enabled": health.enabled,
        "configured": health.configured,
        "ffmpeg_available": health.ffmpeg_available,
        "ffprobe_available": health.ffprobe_available,
        "recording_root_configured": health.recording_root_configured,
        "active_jobs": health.active_jobs,
        "runtime_mode": health.runtime_mode,
        "media_root_exposed": False,
        "cluster_failover_claimed": False,
    }


__all__ = ["router", "shared_sky_media_asset"]

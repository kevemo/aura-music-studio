from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response

from .owner_identity import owner_session_authorized
from .shared_sky_internal_media import SharedSkyInternalMediaError, internal_media
from .shared_sky_transport_domain import transport


router = APIRouter(tags=["Shared Sky Internal Media"])
_PLAYBACK_COOKIE = "shared_sky_playback"


def _bearer(value: str) -> str:
    clean = (value or "").strip()
    if not clean.lower().startswith("bearer "):
        raise HTTPException(401, "Shared Sky playback authorization is required")
    token = clean[7:].strip()
    if not token or len(token) > 2048:
        raise HTTPException(401, "Shared Sky playback authorization is invalid")
    return token


def _cookie_or_bearer(authorization: str, playback_cookie: str) -> str:
    if (authorization or "").strip():
        return _bearer(authorization)
    token = (playback_cookie or "").strip()
    if not token or len(token) > 2048:
        raise HTTPException(401, "Shared Sky playback authorization is required")
    return token


def _owner(request: Request) -> None:
    if not owner_session_authorized(request):
        raise HTTPException(401, "Owner authentication required")


def shared_sky_media_asset(
    broadcast_id: str,
    asset_path: str,
    authorization: str = "",
    playback_cookie: str = "",
):
    """Serve one HLS asset after bearer or scoped-cookie verification.

    This helper remains directly unit-testable; the FastAPI route wrapper supplies the browser
    cookie. Credentials never need to be embedded in the HLS manifest or segment URL.
    """
    token = _cookie_or_bearer(authorization, playback_cookie)
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


@router.get(
    "/shared-sky/media/{broadcast_id}/{asset_path:path}",
    include_in_schema=False,
)
def shared_sky_media_asset_route(
    broadcast_id: str,
    asset_path: str,
    request: Request,
    authorization: str = Header(default="", alias="Authorization"),
):
    return shared_sky_media_asset(
        broadcast_id,
        asset_path,
        authorization=authorization,
        playback_cookie=request.cookies.get(_PLAYBACK_COOKIE, ""),
    )


@router.post(
    "/shared-sky/media/{broadcast_id}/authorize",
    include_in_schema=False,
    status_code=204,
)
def authorize_shared_sky_media(
    broadcast_id: str,
    authorization: str = Header(default="", alias="Authorization"),
):
    """Exchange a valid playback bearer for an HttpOnly same-origin media cookie."""
    token = _bearer(authorization)
    try:
        verified = transport.verify_playback_token(token, expected_broadcast_id=broadcast_id)
        expiry = datetime.fromisoformat(str(verified["expires_at"]).replace("Z", "+00:00"))
    except (ValueError, RuntimeError, KeyError) as exc:
        raise HTTPException(401, "Shared Sky playback authorization is invalid") from exc
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    seconds = max(1, min(600, int((expiry - datetime.now(timezone.utc)).total_seconds())))
    allow_insecure = (os.getenv("SHARED_SKY_ALLOW_INSECURE_PLAYBACK", "0") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    response = Response(status_code=204, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.set_cookie(
        key=_PLAYBACK_COOKIE,
        value=token,
        max_age=seconds,
        path=f"/shared-sky/media/{broadcast_id}/",
        secure=not allow_insecure,
        httponly=True,
        samesite="strict",
    )
    return response


@router.delete(
    "/shared-sky/media/{broadcast_id}/authorize",
    include_in_schema=False,
    status_code=204,
)
def clear_shared_sky_media_authorization(broadcast_id: str):
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        key=_PLAYBACK_COOKIE,
        path=f"/shared-sky/media/{broadcast_id}/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


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


__all__ = [
    "router",
    "shared_sky_media_asset",
    "authorize_shared_sky_media",
    "clear_shared_sky_media_authorization",
]

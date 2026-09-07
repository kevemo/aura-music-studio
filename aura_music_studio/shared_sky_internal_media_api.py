from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from .owner_identity import owner_session_authorized
from .shared_sky_internal_media import SharedSkyInternalMediaError, internal_media
from .shared_sky_live_community import community, optional_member
from .shared_sky_transport_domain import transport


router = APIRouter(tags=["Shared Sky Internal Media"])
_PLAYBACK_COOKIE = "shared_sky_playback"
_ACTIVE_PLAYBACK_STATES = {"live", "degraded", "reconnecting"}


def _allow_insecure_playback() -> bool:
    return (os.getenv("SHARED_SKY_ALLOW_INSECURE_PLAYBACK", "0") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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


def _expiry_seconds(expires_at: str) -> int:
    try:
        expiry = datetime.fromisoformat(str(expires_at or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(401, "Shared Sky playback authorization is invalid") from exc
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    seconds = int((expiry - datetime.now(timezone.utc)).total_seconds())
    if seconds <= 0:
        raise HTTPException(401, "Shared Sky playback authorization has expired")
    return max(1, min(600, seconds))


def _set_playback_cookie(response: Response, broadcast_id: str, token: str, expires_at: str) -> None:
    response.set_cookie(
        key=_PLAYBACK_COOKIE,
        value=token,
        max_age=_expiry_seconds(expires_at),
        path=f"/shared-sky/media/{broadcast_id}/",
        secure=not _allow_insecure_playback(),
        httponly=True,
        samesite="strict",
    )


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
    "/shared-sky/media/{broadcast_id}/bootstrap",
    include_in_schema=False,
)
def bootstrap_shared_sky_media(broadcast_id: str, request: Request):
    """Authorize native-video playback without placing a credential in the media URL.

    The request is evaluated against Chat 4's canonical viewer-access decision first. Only then is
    a fresh Chat 2 playback token minted. The token is stored in a broadcast-scoped HttpOnly cookie
    and the browser is redirected to the built-in manifest path with no secret in the redirect URL.
    """
    member = optional_member(request)
    viewer_user_id = member.user_id if member else None
    try:
        decision = community.access(
            broadcast_id,
            viewer_user_id,
            direct=True,
            owner=owner_session_authorized(request),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        broadcast = community._broadcast(broadcast_id)
        owner_user_id = str(broadcast["user_id"])
        community.rate_limit(
            community.actor_key(request, viewer_user_id),
            "playback_bootstrap",
            limit=120,
            window_seconds=60,
        )
        descriptor = transport.playback(owner_user_id, broadcast_id, ttl=120)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)[:120] or "Shared Sky playback is not permitted") from exc
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky broadcast was not found") from exc
    except Exception as exc:
        raise HTTPException(503, "Shared Sky playback is temporarily unavailable") from exc

    capability = str(getattr(descriptor.get("capability_state"), "value", descriptor.get("capability_state")) or "").lower()
    state = str(descriptor.get("state") or "").lower()
    authorization = descriptor.get("authorization") if isinstance(descriptor.get("authorization"), dict) else {}
    token = str(authorization.get("token") or "")
    expires_at = str(authorization.get("expires_at") or "")
    manifest_url = str(descriptor.get("manifest_url") or "").strip()
    parsed = urlparse(manifest_url)
    manifest_path = parsed.path if parsed.scheme or parsed.netloc else manifest_url.split("?", 1)[0]
    expected_prefix = f"/shared-sky/media/{broadcast_id}/"
    if (
        capability != "ready"
        or state not in _ACTIVE_PLAYBACK_STATES
        or not token
        or not expires_at
        or not manifest_path.startswith(expected_prefix)
        or manifest_path.endswith("/bootstrap")
    ):
        raise HTTPException(503, "Shared Sky first-party browser playback is not ready")

    response = RedirectResponse(
        url=manifest_path,
        status_code=307,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    _set_playback_cookie(response, broadcast_id, token, expires_at)
    return response


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
    except (ValueError, RuntimeError, KeyError) as exc:
        raise HTTPException(401, "Shared Sky playback authorization is invalid") from exc
    response = Response(
        status_code=204,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    _set_playback_cookie(response, broadcast_id, token, str(verified.get("expires_at") or ""))
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
        secure=not _allow_insecure_playback(),
        httponly=True,
        samesite="strict",
    )
    return response


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
    "bootstrap_shared_sky_media",
    "authorize_shared_sky_media",
    "clear_shared_sky_media_authorization",
]

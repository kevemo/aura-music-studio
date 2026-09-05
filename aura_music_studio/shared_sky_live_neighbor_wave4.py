from __future__ import annotations

import importlib
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import shared_sky_live_community as live
from .owner_identity import owner_session_authorized

router = APIRouter(tags=["Shared Sky Viewer Neighbour Wave 4"])

_ACTIVE_TRANSPORT_STATES = {"live", "degraded", "reconnecting"}


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return int(port)
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _same_origin_media_url(request: Request, value: Any) -> str:
    """Accept relative or exact same-origin HTTP(S) media URLs only for cookie playback."""

    raw = str(value or "").strip()
    if not raw or any(ch in raw for ch in ("\n", "\r", "\x00")):
        return ""
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    request_scheme = str(request.url.scheme or "").lower()
    request_host = str(request.url.hostname or "").lower()
    if parsed.scheme.lower() != request_scheme or parsed.hostname.lower() != request_host:
        return ""
    if _effective_port(parsed.scheme.lower(), parsed.port) != _effective_port(
        request_scheme, request.url.port
    ):
        return ""
    return raw


def _access_or_raise(broadcast_id: str, request: Request) -> str | None:
    member = live.optional_member(request)
    user_id = member.user_id if member else None
    try:
        decision = live.community.access(
            broadcast_id,
            user_id,
            direct=True,
            owner=owner_session_authorized(request),
        )
    except KeyError as exc:
        raise HTTPException(404, "Shared Sky LIVE not found") from exc
    if not decision.allowed:
        raise HTTPException(403, decision.reason)
    return user_id


def _chat2_browser_contract(broadcast_id: str, request: Request) -> tuple[dict[str, Any], str]:
    """Resolve Chat 2's canonical raw playback + browser exchange contract without signing locally."""

    try:
        transport_module = importlib.import_module(
            f"{__package__}.shared_sky_transport_domain"
        )
        transport = getattr(transport_module, "transport")
    except (ModuleNotFoundError, AttributeError) as exc:
        raise HTTPException(503, "Shared Sky transport is unavailable") from exc

    try:
        owner_user_id = str(live.community._broadcast(broadcast_id)["user_id"])
        raw = _dict(transport.playback(owner_user_id, broadcast_id))
    except Exception as exc:
        raise HTTPException(503, "Shared Sky playback is unavailable") from exc

    capability = _value(raw.get("capability_state"))
    state = _value(raw.get("state"))
    if capability != "ready" or state not in _ACTIVE_TRANSPORT_STATES:
        raise HTTPException(503, "Shared Sky playback is not ready")

    manifest_url = _same_origin_media_url(request, raw.get("manifest_url"))
    if not manifest_url:
        raise HTTPException(
            503,
            "Shared Sky browser playback requires an exact same-origin media URL",
        )

    auth = _dict(raw.get("authorization"))
    token = str(auth.get("token") or "").strip()
    if _value(auth.get("scheme")) != "bearer" or not token or len(token) > 2048:
        raise HTTPException(503, "Shared Sky playback bearer is unavailable")

    browser = _dict(raw.get("browser_authorization"))
    expected_exchange = f"/shared-sky/media/{broadcast_id}/authorize"
    if (
        _value(browser.get("mode")) != "cookie_exchange"
        or str(browser.get("method") or "").upper() != "POST"
        or str(browser.get("exchange_url") or "") != expected_exchange
        or browser.get("cookie_http_only") is not True
        or browser.get("cookie_path_scoped") is not True
        or browser.get("token_in_manifest_url") is not False
    ):
        raise HTTPException(
            503,
            "Shared Sky secure browser playback exchange is unavailable",
        )

    safe = {
        "available": True,
        "state": "ready" if state == "live" else state,
        "broadcast_id": broadcast_id,
        "mode": raw.get("mode"),
        "manifest_url": manifest_url,
        "browser_authorization_mode": "cookie_exchange",
        "token_expires_at": auth.get("expires_at"),
        "dvr": bool(raw.get("dvr", False)),
        "captions": raw.get("captions") if isinstance(raw.get("captions"), list) else [],
        "renditions": raw.get("renditions") if isinstance(raw.get("renditions"), list) else [],
        "transport_state": state,
        "source": "chat2_secure_cookie_exchange",
    }
    return safe, token


@router.post("/shared-sky/live/api/watch/{broadcast_id}/browser-playback-session")
def create_browser_playback_session(broadcast_id: str, request: Request):
    """Exchange Chat 2's short-lived bearer server-side and return only a safe media descriptor.

    Chat 4 performs viewer visibility/access admission. Chat 2 remains the signing and media-cookie
    authority: its canonical exchange function verifies the bearer and creates the Secure,
    HttpOnly, SameSite=Strict, broadcast-path-scoped cookie. The bearer is never exposed to browser
    JavaScript, HTML/JSON payloads, URLs or storage.
    """

    if request.headers.get("x-shared-sky-playback-intent", "").strip().lower() != "watch":
        raise HTTPException(400, "Explicit Shared Sky Watch playback intent is required")
    _access_or_raise(broadcast_id, request)
    descriptor, token = _chat2_browser_contract(broadcast_id, request)
    try:
        media_api = importlib.import_module(
            f"{__package__}.shared_sky_internal_media_api"
        )
        authorize = getattr(media_api, "authorize_shared_sky_media")
        exchange_response = authorize(
            broadcast_id,
            authorization=f"Bearer {token}",
        )
    except (ModuleNotFoundError, AttributeError) as exc:
        raise HTTPException(
            503,
            "Shared Sky browser media exchange is not installed",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Shared Sky browser media exchange failed") from exc

    set_cookie = exchange_response.headers.get("set-cookie", "")
    if not set_cookie or "httponly" not in set_cookie.lower():
        raise HTTPException(
            503,
            "Shared Sky browser media exchange did not create an HttpOnly cookie",
        )
    response = JSONResponse(
        descriptor,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
    response.headers.append("Set-Cookie", set_cookie)
    return response


class Chat6BattleDisplayAdapter:
    """Read-only Chat 4 projection of Chat 6's canonical viewer-safe Battle state."""

    def __init__(self, viewer_live_battle: Any):
        self.viewer_live_battle = viewer_live_battle

    def state(self, broadcast_id: str, viewer_user_id: str | None) -> dict:
        del viewer_user_id  # Chat 4 has already enforced Watch visibility before this projection.
        try:
            snapshot = self.viewer_live_battle(broadcast_id)
        except Exception:
            return {
                "available": False,
                "reason": "battle_snapshot_unavailable",
                "source": "chat6_battles",
            }
        if not isinstance(snapshot, dict) or not snapshot:
            return {
                "available": False,
                "reason": "no_active_battle",
                "source": "chat6_battles",
            }
        battle = _dict(snapshot.get("battle"))
        if str(battle.get("live_session_id") or "") != broadcast_id:
            return {
                "available": False,
                "reason": "battle_live_session_mismatch",
                "source": "chat6_battles",
            }
        participants = snapshot.get("participants")
        teams = snapshot.get("teams")
        return {
            "available": True,
            "battle_id": battle.get("id"),
            "status": battle.get("status"),
            "mode": battle.get("mode"),
            "participants": list(participants) if isinstance(participants, list) else [],
            "teams": list(teams) if isinstance(teams, list) else [],
            "scores": _dict(snapshot.get("scores")),
            "score_version": int(snapshot.get("score_version") or 0),
            "event_cursor": int(snapshot.get("event_cursor") or 0),
            "remaining_ms": snapshot.get("remaining_ms"),
            "current_round": snapshot.get("current_round"),
            "result": snapshot.get("result"),
            "source": "chat6_viewer_snapshot",
        }


def configure_wave4_neighbor_adapters() -> dict[str, Any]:
    """Register neighbour adapters only when their explicit public compatibility seams exist."""

    state: dict[str, Any] = {
        "chat2_browser_playback": {
            "state": "pending",
            "reason": "chat2_cookie_exchange_not_merged",
        },
        "chat6_battle_display": {
            "state": "pending",
            "reason": "chat6_viewer_live_lookup_not_merged",
        },
    }

    try:
        transport_module = importlib.import_module(
            f"{__package__}.shared_sky_transport_domain"
        )
        transport = getattr(transport_module, "transport")
        # Merely having Chat 2's existing transport is not enough. The canonical playback method
        # must explicitly advertise the secure browser cookie-exchange contract at runtime.
        if transport is not None:
            state["chat2_browser_playback"] = {
                "state": "runtime_probe_required",
                "source": "shared_sky_transport_domain.transport.playback",
            }
    except (ModuleNotFoundError, AttributeError):
        pass

    try:
        battle_api = importlib.import_module(f"{__package__}.shared_sky_battle_api")
        viewer_live_battle = getattr(battle_api, "viewer_live_battle")
        live.register_battle_display_adapter(
            Chat6BattleDisplayAdapter(viewer_live_battle)
        )
        state["chat6_battle_display"] = {
            "state": "registered",
            "source": "shared_sky_battle_api.viewer_live_battle",
            "authority": "chat6_read_only",
        }
        try:
            integrations = importlib.import_module(
                f"{__package__}.shared_sky_live_integrations"
            )
            integration_status = getattr(integrations, "_INTEGRATION_STATUS", None)
            if isinstance(integration_status, dict):
                integration_status["chat6_battles"] = {
                    "state": "registered",
                    "source": "shared_sky_battle_api.viewer_live_battle",
                    "authority": "chat6_read_only",
                }
        except Exception:
            pass
    except (ModuleNotFoundError, AttributeError):
        pass

    return state


@router.get("/shared-sky/live/api/watch/{broadcast_id}/battle-display")
def refresh_battle_display(broadcast_id: str, request: Request):
    user_id = _access_or_raise(broadcast_id, request)
    try:
        return live._battle_adapter.state(broadcast_id, user_id)
    except Exception as exc:
        raise HTTPException(503, "Shared Sky Battle display is unavailable") from exc


__all__ = [
    "router",
    "Chat6BattleDisplayAdapter",
    "configure_wave4_neighbor_adapters",
    "create_browser_playback_session",
    "refresh_battle_display",
    "_same_origin_media_url",
]

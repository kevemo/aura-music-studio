from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .esp_niche import require_esp_social_member
from .esp_social_oauth import _TIKTOK_AUTHORIZE, _provider_config, _vault, oauth_credential_id
from .social_management import SocialConnection, SocialHouseStore

router = APIRouter(tags=["esp-social-tiktok-scope-upgrade"])

_VIDEO_LIST_SCOPE = "video.list"
_REQUIRED_EXISTING_SCOPES = ("user.info.basic", "video.publish")


def _enabled() -> bool:
    return (os.getenv("TIKTOK_OAUTH_VIDEO_LIST_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _clean_scopes(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = [str(item).strip() for item in value]
    else:
        raw = str(value or "").replace(",", " ").split()
    return list(dict.fromkeys(item for item in raw if item))


def _house(space_id: str):
    try:
        return SocialHouseStore().load(space_id)
    except FileNotFoundError as exc:
        raise KeyError("ESP Social House not found") from exc


def _member_tiktok_connection(house, connection_id: str | None = None) -> SocialConnection | None:
    rows = [
        item
        for item in house.connections
        if item.platform == "tiktok"
        and item.state == "connected"
        and item.supports_auto_publish is True
        and item.metadata.get("oauth_verified") is True
        and item.metadata.get("oauth_provider") == "tiktok"
        and oauth_credential_id(item.token_secret_ref)
    ]
    if connection_id:
        return next((item for item in rows if item.id == connection_id), None)
    return rows[0] if rows else None


def _credential_for(user_id: str, connection: SocialConnection) -> tuple[str, dict]:
    credential_id = oauth_credential_id(connection.token_secret_ref)
    if not credential_id:
        raise PermissionError("TikTok connection is not backed by a member OAuth credential")
    record = _vault().load(user_id, credential_id)
    if not record:
        raise PermissionError("TikTok OAuth credential is unavailable for this ESP member")
    if str(record.get("provider") or "") != "tiktok":
        raise PermissionError("Stored OAuth credential provider does not match TikTok")
    connection_account = str(connection.account_external_id or "").strip()
    credential_account = str(record.get("account_external_id") or "").strip()
    if not connection_account or not credential_account or connection_account != credential_account:
        raise PermissionError("TikTok OAuth credential does not match the selected connected account")
    return credential_id, record


def video_list_status(user_id: str, space_id: str, connection_id: str | None = None) -> dict:
    house = _house(space_id)
    connection = _member_tiktok_connection(house, connection_id)
    if not connection:
        return {
            "platform": "tiktok",
            "app_scope_enabled": _enabled(),
            "connected": False,
            "granted": False,
            "ready_to_request": False,
            "connection_id": None,
            "required_scope": _VIDEO_LIST_SCOPE,
            "tokens_exposed": False,
            "reason": "Connect TikTok with member OAuth before requesting the separate video.list analytics permission.",
        }
    _credential_id, record = _credential_for(user_id, connection)
    scopes = _clean_scopes(record.get("scopes"))
    granted = _VIDEO_LIST_SCOPE in scopes
    app_enabled = _enabled()
    missing_existing = [scope for scope in _REQUIRED_EXISTING_SCOPES if scope not in scopes]
    ready = app_enabled and not granted and not missing_existing
    if granted:
        reason = "The creator has already authorised TikTok video.list for this connected account."
    elif missing_existing:
        reason = "The existing TikTok publishing credential is incomplete; reconnect TikTok before requesting analytics permission."
    elif not app_enabled:
        reason = "TikTok video.list consent is disabled until ESP confirms the TikTok developer app has been approved for that scope."
    else:
        reason = "The ESP TikTok app is marked ready for video.list. The creator must still explicitly authorise that additional scope."
    return {
        "platform": "tiktok",
        "app_scope_enabled": app_enabled,
        "connected": True,
        "granted": granted,
        "ready_to_request": ready,
        "connection_id": connection.id,
        "account_label": connection.account_label,
        "required_scope": _VIDEO_LIST_SCOPE,
        "existing_scopes": scopes,
        "tokens_exposed": False,
        "reason": reason,
    }


def video_list_authorization_url(user_id: str, space_id: str, connection_id: str) -> str:
    if not _enabled():
        raise RuntimeError(
            "TikTok video.list consent is disabled until ESP confirms the developer app has been approved for that scope"
        )
    house = _house(space_id)
    connection = _member_tiktok_connection(house, connection_id)
    if not connection:
        raise PermissionError("A connected publishing-capable member-authorised TikTok account is required")
    credential_id, record = _credential_for(user_id, connection)
    scopes = _clean_scopes(record.get("scopes"))
    missing_existing = [scope for scope in _REQUIRED_EXISTING_SCOPES if scope not in scopes]
    if missing_existing:
        raise PermissionError(
            "Existing TikTok authorization is missing required publishing scopes; reconnect TikTok first"
        )
    if _VIDEO_LIST_SCOPE in scopes:
        return "/command-center/social/connections?scope=tiktok-video-list-already-granted"

    cfg = _provider_config("tiktok")
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise RuntimeError("TikTok OAuth application credentials are not configured")
    if not cfg["redirect_uri"].startswith("https://"):
        raise RuntimeError("TikTok Web OAuth redirect URI must use HTTPS")

    requested = list(dict.fromkeys([*scopes, _VIDEO_LIST_SCOPE]))
    state = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    vault = _vault()
    with sqlite3.connect(vault.path) as con:
        con.execute(
            "DELETE FROM esp_social_oauth_state WHERE created_at<?",
            ((now - timedelta(minutes=30)).isoformat(),),
        )
        con.execute(
            """DELETE FROM esp_social_oauth_state
               WHERE user_id=? AND provider='tiktok' AND connection_id=?""",
            (user_id, connection.id),
        )
        con.execute(
            """INSERT INTO esp_social_oauth_state
               (state,user_id,space_id,provider,connection_id,credential_id,scopes_json,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                state,
                user_id,
                space_id,
                "tiktok",
                connection.id,
                credential_id,
                json.dumps(requested),
                now.isoformat(),
            ),
        )

    return _TIKTOK_AUTHORIZE + "?" + urlencode(
        {
            "client_key": cfg["client_id"],
            "scope": ",".join(requested),
            "response_type": "code",
            "redirect_uri": cfg["redirect_uri"],
            "state": state,
        }
    )


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


@router.get("/oauth/tiktok/video-list/status")
def tiktok_video_list_status(space_id: str, request: Request, connection_id: str = ""):
    member = _member(request)
    try:
        return video_list_status(member.user_id, space_id, connection_id or None)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/oauth/tiktok/video-list/start", include_in_schema=False)
def tiktok_video_list_start(space_id: str, connection_id: str, request: Request):
    member = _member(request)
    try:
        url = video_list_authorization_url(member.user_id, space_id, connection_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(url, status_code=303)


__all__ = [
    "router",
    "video_list_authorization_url",
    "video_list_status",
]

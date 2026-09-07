from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .esp_niche import require_esp_social_member
from .esp_social_oauth import SocialOAuthVault
from .esp_social_provider_adapters import ProviderAdapterError
from .social_management import ActivityEvent, SocialConnection, SocialHouseStore, utc_now

router = APIRouter(tags=["ESP Social Facebook OAuth"])

_GRAPH_VERSION_RE = re.compile(r"^v[0-9]+(?:\.[0-9]+)?$")
_PAGE_ID_RE = re.compile(r"^[0-9]{2,40}$")
_REQUIRED_SCOPES = ["pages_show_list", "pages_manage_posts", "pages_read_engagement"]
_RETRYABLE_HTTP = {408, 425, 429}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timeout() -> int:
    try:
        value = int(os.getenv("AURA_SOCIAL_OAUTH_HTTP_TIMEOUT", "25"))
    except ValueError:
        value = 25
    return max(5, min(value, 120))


def _config() -> dict[str, str]:
    version = (
        os.getenv("AURA_FACEBOOK_GRAPH_VERSION", "").strip()
        or os.getenv("AURA_META_GRAPH_VERSION", "").strip()
    )
    if not _GRAPH_VERSION_RE.fullmatch(version):
        raise RuntimeError(
            "AURA_FACEBOOK_GRAPH_VERSION (or AURA_META_GRAPH_VERSION) must be configured to a version such as v23.0"
        )
    app_id = (
        os.getenv("FACEBOOK_OAUTH_APP_ID", "").strip()
        or os.getenv("META_OAUTH_APP_ID", "").strip()
    )
    app_secret = (
        os.getenv("FACEBOOK_OAUTH_APP_SECRET", "").strip()
        or os.getenv("META_OAUTH_APP_SECRET", "").strip()
    )
    if not app_id or not app_secret:
        raise RuntimeError("Facebook OAuth application credentials are not configured")
    base = (os.getenv("LSS_PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    callback = f"{base}/command-center/api/social/oauth/facebook/callback"
    redirect_uri = (os.getenv("FACEBOOK_OAUTH_REDIRECT_URI") or callback).strip()
    return {
        "version": version,
        "app_id": app_id,
        "app_secret": app_secret,
        "redirect_uri": redirect_uri,
        "graph_base": f"https://graph.facebook.com/{version}",
        "authorize_url": f"https://www.facebook.com/{version}/dialog/oauth",
    }


def _checked(response: requests.Response, action: str) -> requests.Response:
    if response.ok:
        return response
    status = int(response.status_code)
    raise ProviderAdapterError(
        f"Facebook OAuth {action} failed with HTTP {status}",
        retryable=status >= 500 or status in _RETRYABLE_HTTP,
    )


def _safe_json(response: requests.Response, action: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderAdapterError(
            f"Facebook OAuth {action} returned an invalid response",
            retryable=False,
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderAdapterError(
            f"Facebook OAuth {action} returned an invalid response",
            retryable=False,
        )
    return payload


class FacebookPageOAuthService:
    """Member-scoped Facebook Login flow for one explicitly selected Page.

    The caller must provide the numeric Page ID before redirecting to Meta. Completion
    verifies the granted permissions and then selects only that Page from /me/accounts.
    The service never silently chooses a Page when the member controls multiple Pages.
    """

    def __init__(self, vault: SocialOAuthVault | None = None):
        self.vault = vault or SocialOAuthVault()
        self.timeout = _timeout()
        self.ensure_schema()

    def _connect(self):
        con = sqlite3.connect(self.vault.path)
        con.row_factory = sqlite3.Row
        return con

    def ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS esp_social_facebook_oauth_state (
                       state TEXT PRIMARY KEY,
                       user_id TEXT NOT NULL,
                       space_id TEXT NOT NULL,
                       page_id TEXT NOT NULL,
                       connection_id TEXT NOT NULL,
                       credential_id TEXT NOT NULL,
                       created_at TEXT NOT NULL
                   )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_esp_social_facebook_oauth_state_user ON esp_social_facebook_oauth_state(user_id, created_at)"
            )

    def diagnostics(self) -> dict[str, Any]:
        try:
            cfg = _config()
            configured = True
            redirect_uri = cfg["redirect_uri"]
            graph_version = cfg["version"]
        except RuntimeError:
            configured = False
            redirect_uri = ""
            graph_version = ""
        try:
            SocialOAuthVault._fernet()
            encrypted_vault_ready = True
        except RuntimeError:
            encrypted_vault_ready = False
        return {
            "provider": "facebook",
            "configured": configured,
            "encrypted_vault_ready": encrypted_vault_ready,
            "graph_version": graph_version,
            "redirect_uri": redirect_uri,
            "required_scopes": list(_REQUIRED_SCOPES),
            "page_selection": "explicit_numeric_page_id",
            "tokens_exposed_to_browser": False,
            "supports_auto_publish": True,
            "supports_inbox": False,
            "supports_analytics": False,
        }

    def create_state(self, *, user_id: str, space_id: str, page_id: str) -> tuple[str, str, str]:
        clean_page = (page_id or "").strip()
        if not _PAGE_ID_RE.fullmatch(clean_page):
            raise ValueError("A verified numeric Facebook Page ID is required")
        SocialHouseStore().load(space_id)
        self.vault._fernet()
        state = secrets.token_urlsafe(48)
        connection_id = f"conn_{uuid4().hex}"
        credential_id = f"socialcred_{uuid4().hex}"
        now = _now()
        with self._connect() as con:
            con.execute(
                "DELETE FROM esp_social_facebook_oauth_state WHERE created_at<?",
                (_iso(now - timedelta(minutes=30)),),
            )
            con.execute(
                """INSERT INTO esp_social_facebook_oauth_state
                   (state,user_id,space_id,page_id,connection_id,credential_id,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    state,
                    user_id,
                    space_id,
                    clean_page,
                    connection_id,
                    credential_id,
                    _iso(now),
                ),
            )
        return state, connection_id, credential_id

    def consume_state(self, *, user_id: str, state: str) -> dict[str, str]:
        clean_state = (state or "").strip()
        if not clean_state or len(clean_state) > 512:
            raise PermissionError("Facebook OAuth state is invalid or missing")
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_social_facebook_oauth_state WHERE state=? AND user_id=?",
                (clean_state, user_id),
            ).fetchone()
            if row:
                con.execute(
                    "DELETE FROM esp_social_facebook_oauth_state WHERE state=?",
                    (clean_state,),
                )
        if not row:
            raise PermissionError(
                "Facebook OAuth state is invalid, expired, or belongs to another member"
            )
        created = _parse_iso(row["created_at"])
        if not created or created < _now() - timedelta(minutes=15):
            raise PermissionError("Facebook OAuth state has expired")
        return {
            "space_id": str(row["space_id"]),
            "page_id": str(row["page_id"]),
            "connection_id": str(row["connection_id"]),
            "credential_id": str(row["credential_id"]),
        }

    def authorization_url(self, *, user_id: str, space_id: str, page_id: str) -> str:
        cfg = _config()
        state, _connection_id, _credential_id = self.create_state(
            user_id=user_id,
            space_id=space_id,
            page_id=page_id,
        )
        return cfg["authorize_url"] + "?" + urlencode(
            {
                "client_id": cfg["app_id"],
                "redirect_uri": cfg["redirect_uri"],
                "response_type": "code",
                "scope": ",".join(_REQUIRED_SCOPES),
                "state": state,
            }
        )

    def _exchange_user_token(self, *, code: str, cfg: dict[str, str]) -> str:
        clean_code = (code or "").strip()
        if not clean_code or len(clean_code) > 4096:
            raise ValueError("Facebook OAuth authorization code is missing or invalid")
        try:
            response = requests.post(
                f"{cfg['graph_base']}/oauth/access_token",
                data={
                    "client_id": cfg["app_id"],
                    "client_secret": cfg["app_secret"],
                    "redirect_uri": cfg["redirect_uri"],
                    "code": clean_code,
                },
                timeout=self.timeout,
            )
            _checked(response, "code exchange")
        except requests.RequestException as exc:
            raise ProviderAdapterError(
                "Facebook OAuth code exchange could not reach the provider",
                retryable=True,
            ) from exc
        payload = _safe_json(response, "code exchange")
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise ProviderAdapterError(
                "Facebook OAuth code exchange did not return an access token",
                retryable=False,
            )
        return access_token

    def _verify_permissions(self, *, user_token: str, cfg: dict[str, str]) -> list[str]:
        try:
            response = requests.get(
                f"{cfg['graph_base']}/me/permissions",
                headers={"Authorization": f"Bearer {user_token}"},
                timeout=self.timeout,
            )
            _checked(response, "permission verification")
        except requests.RequestException as exc:
            raise ProviderAdapterError(
                "Facebook OAuth permission verification could not reach the provider",
                retryable=True,
            ) from exc
        data = _safe_json(response, "permission verification").get("data") or []
        granted = {
            str(item.get("permission") or "").strip()
            for item in data
            if isinstance(item, dict) and str(item.get("status") or "").lower() == "granted"
        }
        missing = [scope for scope in _REQUIRED_SCOPES if scope not in granted]
        if missing:
            raise PermissionError(
                f"Required Facebook Page publishing permission was not granted: {', '.join(missing)}"
            )
        return [scope for scope in _REQUIRED_SCOPES if scope in granted]

    def _resolve_page(
        self,
        *,
        user_token: str,
        target_page_id: str,
        cfg: dict[str, str],
    ) -> tuple[str, str, list[str]]:
        try:
            response = requests.get(
                f"{cfg['graph_base']}/me/accounts",
                headers={"Authorization": f"Bearer {user_token}"},
                params={"fields": "id,name,access_token,tasks", "limit": "100"},
                timeout=self.timeout,
            )
            _checked(response, "Page verification")
        except requests.RequestException as exc:
            raise ProviderAdapterError(
                "Facebook OAuth Page verification could not reach the provider",
                retryable=True,
            ) from exc
        pages = _safe_json(response, "Page verification").get("data") or []
        matching = [
            page
            for page in pages
            if isinstance(page, dict) and str(page.get("id") or "").strip() == target_page_id
        ]
        if len(matching) != 1:
            raise PermissionError(
                "The authorised Facebook account does not expose the explicitly selected Page"
            )
        page = matching[0]
        page_token = str(page.get("access_token") or "").strip()
        if not page_token:
            raise PermissionError("Meta did not provide a Page access token for the selected Page")
        tasks = [str(task).strip().upper() for task in page.get("tasks") or [] if str(task).strip()]
        if tasks and not ({"CREATE_CONTENT", "MANAGE"} & set(tasks)):
            raise PermissionError(
                "The authorised Facebook account does not have Page content-management authority"
            )
        label = str(page.get("name") or target_page_id).strip()[:200]
        return page_token, label, tasks

    def complete(self, *, user_id: str, state: str, code: str) -> SocialConnection:
        pending = self.consume_state(user_id=user_id, state=state)
        cfg = _config()
        user_token = self._exchange_user_token(code=code, cfg=cfg)
        scopes = self._verify_permissions(user_token=user_token, cfg=cfg)
        page_token, page_label, tasks = self._resolve_page(
            user_token=user_token,
            target_page_id=pending["page_id"],
            cfg=cfg,
        )
        token_record = {
            "access_token": page_token,
            "token_type": "page_access_token",
            "issued_at": _iso(_now()),
        }
        provider_meta = {
            "oauth_flow": "facebook_login_page",
            "facebook_page_id": pending["page_id"],
            "facebook_page_tasks": tasks,
            "manual_reconnect_on_token_expiry": True,
        }
        self.vault.save(
            user_id=user_id,
            credential_id=pending["credential_id"],
            provider="facebook",  # type: ignore[arg-type]
            token=token_record,
            scopes=scopes,
            account_external_id=pending["page_id"],
            account_label=page_label,
            metadata=provider_meta,
        )
        connection = SocialConnection(
            id=pending["connection_id"],
            platform="facebook",
            account_label=page_label,
            account_external_id=pending["page_id"],
            state="connected",
            supports_auto_publish=True,
            supports_analytics=False,
            supports_inbox=False,
            token_secret_ref=f"social-oauth://{pending['credential_id']}",
            metadata={
                "publishing_adapter": "facebook_pages_graph",
                "publishing_adapter_active": True,
                "oauth_provider": "facebook",
                "oauth_scopes": scopes,
                "oauth_verified": True,
                **provider_meta,
            },
        )
        store = SocialHouseStore()
        house = store.connect_placeholder(pending["space_id"], connection)
        house.activity.append(
            ActivityEvent(
                actor=user_id,
                action="social_oauth_connected",
                entity_type="connection",
                entity_id=connection.id,
                detail=f"facebook:{pending['page_id']}",
            )
        )
        store.save(house)
        return connection

    def disconnect(self, *, user_id: str, space_id: str, connection_id: str) -> bool:
        store = SocialHouseStore()
        house = store.load(space_id)
        connection = next((item for item in house.connections if item.id == connection_id), None)
        if connection is None:
            raise KeyError(connection_id)
        if connection.platform != "facebook":
            raise ValueError("Selected social connection is not a Facebook Page connection")
        secret_ref = (connection.token_secret_ref or "").strip()
        credential_id = secret_ref.removeprefix("social-oauth://") if secret_ref.startswith("social-oauth://") else ""
        if not credential_id:
            raise ValueError("Facebook Page connection is not backed by a member OAuth credential")
        record = self.vault.load(user_id, credential_id)
        if record and str(record.get("provider") or "") != "facebook":
            raise PermissionError("Stored OAuth credential provider does not match Facebook")
        if record:
            self.vault.delete(user_id, credential_id)
        connection.state = "not_connected"
        connection.supports_auto_publish = False
        connection.token_secret_ref = None
        connection.metadata["publishing_adapter_active"] = False
        connection.metadata["oauth_verified"] = False
        connection.updated_at = utc_now()
        house.activity.append(
            ActivityEvent(
                actor=user_id,
                action="social_oauth_disconnected",
                entity_type="connection",
                entity_id=connection.id,
                detail="facebook",
            )
        )
        store.save(house)
        return True


_service_instance: FacebookPageOAuthService | None = None


def _service() -> FacebookPageOAuthService:
    global _service_instance
    candidate = SocialOAuthVault()
    if _service_instance is None or _service_instance.vault.path != candidate.path:
        _service_instance = FacebookPageOAuthService(candidate)
    return _service_instance


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


@router.get("/oauth/facebook/capability")
def facebook_oauth_capability(request: Request):
    _member(request)
    return _service().diagnostics()


@router.get("/oauth/facebook/start", include_in_schema=False)
def facebook_oauth_start(space_id: str, page_id: str, request: Request):
    member = _member(request)
    try:
        url = _service().authorization_url(
            user_id=member.user_id,
            space_id=space_id,
            page_id=page_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return RedirectResponse(url, status_code=303)


@router.get(
    "/oauth/facebook/callback",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def facebook_oauth_callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    error_description: str = "",
):
    member = _member(request)
    if error:
        message = escape((error_description or error)[:500])
        return HTMLResponse(
            "<!doctype html><title>Connection not completed</title>"
            "<h1>Connection not completed</h1>"
            f"<p>{message}</p>"
            "<p><a href='/command-center/social'>Return to ESP Social Management</a></p>",
            status_code=400,
        )
    try:
        connection = _service().complete(
            user_id=member.user_id,
            state=state,
            code=code,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ProviderAdapterError as exc:
        raise HTTPException(503 if exc.retryable else 400, str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, f"Facebook Page connection failed: {exc}") from exc
    return HTMLResponse(
        "<!doctype html><title>Facebook Page connected</title>"
        "<h1>Facebook Page connected</h1>"
        f"<p>{escape(connection.account_label)} is authorised for ESP Social Management.</p>"
        "<p>No OAuth token was exposed to this browser page.</p>"
        "<p><a href='/command-center/social'>Return to ESP Social Management</a></p>"
    )


@router.post("/oauth/facebook/disconnect")
def facebook_oauth_disconnect(space_id: str, connection_id: str, request: Request):
    member = _member(request)
    try:
        _service().disconnect(
            user_id=member.user_id,
            space_id=space_id,
            connection_id=connection_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Social connection not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"disconnected": True, "connection_id": connection_id, "tokens_exposed": False}


__all__ = ["FacebookPageOAuthService", "router"]

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode
from uuid import uuid4

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .esp_niche import require_esp_social_member
from .esp_social_oauth import SocialOAuthVault, oauth_credential_id
from .esp_social_provider_adapters import ProviderAdapterError
from .social_management import ActivityEvent, SocialConnection, SocialHouseStore, utc_now

router = APIRouter(tags=["ESP Social Threads OAuth"])

_THREADS_AUTHORIZE = "https://threads.net/oauth/authorize"
_THREADS_GRAPH = "https://graph.threads.net"
_REQUIRED_SCOPES = ("threads_basic", "threads_content_publish")
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
    base = (os.getenv("LSS_PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    callback = f"{base}/command-center/api/social/oauth/threads/callback"
    return {
        "client_id": (
            os.getenv("THREADS_OAUTH_APP_ID")
            or os.getenv("META_THREADS_APP_ID")
            or ""
        ).strip(),
        "client_secret": (
            os.getenv("THREADS_OAUTH_APP_SECRET")
            or os.getenv("META_THREADS_APP_SECRET")
            or ""
        ).strip(),
        "redirect_uri": (os.getenv("THREADS_OAUTH_REDIRECT_URI") or callback).strip(),
    }


def _require_config() -> dict[str, str]:
    cfg = _config()
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise RuntimeError("Threads OAuth application credentials are not configured")
    if not cfg["redirect_uri"].startswith("https://"):
        raise RuntimeError("Threads OAuth redirect URI must use HTTPS")
    return cfg


def _checked(response: requests.Response, action: str) -> requests.Response:
    if response.ok:
        return response
    status = int(response.status_code)
    raise ProviderAdapterError(
        f"Threads OAuth {action} failed with HTTP {status}",
        retryable=status >= 500 or status in _RETRYABLE_HTTP,
    )


def _network(action: str, exc: requests.RequestException) -> ProviderAdapterError:
    return ProviderAdapterError(
        f"Threads OAuth {action} could not reach Meta",
        retryable=True,
    )


class ThreadsOAuthState:
    """Threads-only CSRF state stored beside the existing encrypted Social OAuth vault.

    The core OAuth helper intentionally supports only its original provider set. Threads therefore
    writes the same state-table schema directly with provider='threads' rather than widening generic
    provider behavior or weakening another provider's tested flow.
    """

    def __init__(self, vault: SocialOAuthVault):
        self.vault = vault

    def create(self, user_id: str, space_id: str) -> tuple[str, str, str]:
        self.vault._fernet()  # Enforce encrypted-vault readiness before redirecting a member.
        SocialHouseStore().load(space_id)
        state = secrets.token_urlsafe(48)
        connection_id = f"conn_{uuid4().hex}"
        credential_id = f"socialcred_{uuid4().hex}"
        now = _now()
        with self.vault._connect() as con:
            con.execute(
                "DELETE FROM esp_social_oauth_state WHERE created_at<?",
                (_iso(now - timedelta(minutes=30)),),
            )
            con.execute(
                """INSERT INTO esp_social_oauth_state
                   (state,user_id,space_id,provider,connection_id,credential_id,scopes_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    state,
                    user_id,
                    space_id,
                    "threads",
                    connection_id,
                    credential_id,
                    json.dumps(list(_REQUIRED_SCOPES)),
                    _iso(now),
                ),
            )
        return state, connection_id, credential_id

    def consume(self, user_id: str, state: str) -> dict:
        clean = (state or "").strip()
        if not clean or len(clean) > 512:
            raise PermissionError("Threads OAuth state is invalid or missing")
        with self.vault._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_social_oauth_state WHERE state=? AND user_id=? AND provider='threads'",
                (clean, user_id),
            ).fetchone()
            if row:
                con.execute("DELETE FROM esp_social_oauth_state WHERE state=?", (clean,))
        if not row:
            raise PermissionError("Threads OAuth state is invalid, expired, or belongs to another member")
        created = _parse_iso(row["created_at"])
        if not created or created < _now() - timedelta(minutes=15):
            raise PermissionError("Threads OAuth state has expired")
        return {
            "space_id": row["space_id"],
            "connection_id": row["connection_id"],
            "credential_id": row["credential_id"],
            "scopes": json.loads(row["scopes_json"] or "[]"),
        }


class ThreadsOAuthService:
    def __init__(self, vault: SocialOAuthVault | None = None):
        self.vault = vault or SocialOAuthVault()
        self.state = ThreadsOAuthState(self.vault)
        self.timeout = _timeout()

    def authorization_url(self, *, user_id: str, space_id: str) -> str:
        cfg = _require_config()
        state, _connection_id, _credential_id = self.state.create(user_id, space_id)
        return _THREADS_AUTHORIZE + "?" + urlencode(
            {
                "client_id": cfg["client_id"],
                "redirect_uri": cfg["redirect_uri"],
                "scope": ",".join(_REQUIRED_SCOPES),
                "response_type": "code",
                "state": state,
            }
        )

    def _exchange(self, cfg: dict[str, str], code: str) -> tuple[dict, str, str, str]:
        try:
            short_response = requests.post(
                f"{_THREADS_GRAPH}/oauth/access_token",
                params={
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": cfg["redirect_uri"],
                },
                timeout=self.timeout,
            )
            _checked(short_response, "code exchange")
            short = short_response.json() or {}
            short_access = str(short.get("access_token") or "").strip()
            short_user_id = str(short.get("user_id") or "").strip()
            if not short_access or not short_user_id:
                raise ProviderAdapterError(
                    "Threads code exchange did not return access_token and user_id",
                    retryable=False,
                )

            long_response = requests.get(
                f"{_THREADS_GRAPH}/access_token",
                params={
                    "grant_type": "th_exchange_token",
                    "client_secret": cfg["client_secret"],
                    "access_token": short_access,
                },
                timeout=self.timeout,
            )
            _checked(long_response, "long-lived token exchange")
            long_token = long_response.json() or {}
            access = str(long_token.get("access_token") or "").strip()
            if not access:
                raise ProviderAdapterError(
                    "Threads long-lived token exchange did not return an access token",
                    retryable=False,
                )

            app_response = requests.get(
                f"{_THREADS_GRAPH}/oauth/access_token",
                params={
                    "grant_type": "client_credentials",
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                },
                timeout=self.timeout,
            )
            _checked(app_response, "app-token verification setup")
            app_access = str((app_response.json() or {}).get("access_token") or "").strip()
            if not app_access:
                raise ProviderAdapterError("Threads app-token response was invalid", retryable=False)

            debug_response = requests.get(
                f"{_THREADS_GRAPH}/debug_token",
                params={"input_token": access},
                headers={"Authorization": f"Bearer {app_access}"},
                timeout=self.timeout,
            )
            _checked(debug_response, "permission verification")
            debug = (debug_response.json() or {}).get("data") or {}
            if debug.get("is_valid") is not True:
                raise PermissionError("Threads returned an invalid user access token")
            granted = {str(value) for value in (debug.get("scopes") or [])}
            missing = [scope for scope in _REQUIRED_SCOPES if scope not in granted]
            if missing:
                raise PermissionError(
                    f"Required Threads publishing permission was not granted: {', '.join(missing)}"
                )

            profile_response = requests.get(
                f"{_THREADS_GRAPH}/me",
                params={"fields": "id,username,name"},
                headers={"Authorization": f"Bearer {access}"},
                timeout=self.timeout,
            )
            _checked(profile_response, "profile verification")
        except requests.RequestException as exc:
            raise _network("connection", exc) from exc

        profile = profile_response.json() or {}
        external_id = str(profile.get("id") or short_user_id).strip()
        if not external_id:
            raise ProviderAdapterError("Threads account identity could not be verified", retryable=False)
        username = str(profile.get("username") or "").strip()
        name = str(profile.get("name") or "").strip()
        label = (f"@{username}" if username else name or f"Threads {external_id[-8:]}")[:200]
        token = dict(long_token)
        token["issued_at"] = _iso(_now())
        token["expires_at"] = _iso(
            _now() + timedelta(seconds=max(60, int(long_token.get("expires_in") or 5184000)))
        )
        return token, external_id, label, username

    def complete(self, *, user_id: str, state: str, code: str) -> SocialConnection:
        pending = self.state.consume(user_id, state)
        cfg = _require_config()
        clean_code = (code or "").strip()
        if not clean_code or len(clean_code) > 4096:
            raise ValueError("Threads OAuth authorization code is missing or invalid")
        token, external_id, label, username = self._exchange(cfg, clean_code)
        credential_id = pending["credential_id"]
        self.vault.save(
            user_id=user_id,
            credential_id=credential_id,
            provider="threads",  # type: ignore[arg-type] -- vault storage accepts provider strings.
            token=token,
            scopes=list(_REQUIRED_SCOPES),
            account_external_id=external_id,
            account_label=label,
            metadata={
                "oauth_flow": "threads_authorization_code",
                "threads_username": username,
                "long_lived_token": True,
            },
        )
        connection = SocialConnection(
            id=pending["connection_id"],
            platform="threads",
            account_label=label,
            account_external_id=external_id,
            state="connected",
            supports_auto_publish=True,
            supports_analytics=False,
            supports_inbox=False,
            token_secret_ref=f"social-oauth://{credential_id}",
            metadata={
                "publishing_adapter": "threads_graph",
                "publishing_adapter_active": True,
                "oauth_provider": "threads",
                "oauth_scopes": list(_REQUIRED_SCOPES),
                "oauth_verified": True,
                "oauth_flow": "threads_authorization_code",
                "threads_username": username,
                "long_lived_token": True,
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
                detail=f"threads:{external_id}",
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
        if connection.platform != "threads":
            raise ValueError("Selected social connection is not a Threads OAuth connection")
        credential_id = oauth_credential_id(connection.token_secret_ref)
        if not credential_id:
            raise ValueError("Threads connection is not backed by an encrypted member OAuth credential")
        record = self.vault.load(user_id, credential_id)
        if record and str(record.get("provider")) != "threads":
            raise PermissionError("Stored OAuth credential provider does not match Threads")
        self.vault.delete(user_id, credential_id)
        connection.state = "not_connected"
        connection.supports_auto_publish = False
        connection.token_secret_ref = None
        connection.metadata["publishing_adapter_active"] = False
        connection.metadata["oauth_verified"] = False
        connection.metadata["local_credential_deleted"] = True
        connection.updated_at = utc_now()
        house.activity.append(
            ActivityEvent(
                actor=user_id,
                action="social_oauth_disconnected",
                entity_type="connection",
                entity_id=connection.id,
                detail="threads",
            )
        )
        store.save(house)
        return True


def install_threads_oauth_token_extension() -> bool:
    """Teach the existing encrypted vault to refresh Threads long-lived tokens only.

    The wrapper delegates byte-for-byte behavior for TikTok, Instagram and YouTube to the existing
    tested method. Threads tokens are refreshed only near expiry and only while still unexpired.
    """
    current = SocialOAuthVault.access_token
    if getattr(current, "_threads_oauth_extension", False):
        return False
    original = current

    def access_token_with_threads(self: SocialOAuthVault, user_id: str, credential_id: str) -> str:
        record = self.load(user_id, credential_id)
        if not record or str(record.get("provider")) != "threads":
            return original(self, user_id, credential_id)
        token = dict(record.get("token") or {})
        access = str(token.get("access_token") or "").strip()
        if not access:
            raise ProviderAdapterError(
                "Threads OAuth credential has no access token; reconnect Threads",
                retryable=False,
            )
        expiry = _parse_iso(str(token.get("expires_at") or ""))
        if not expiry or expiry > _now() + timedelta(minutes=5):
            return access
        if expiry <= _now():
            raise ProviderAdapterError(
                "Threads long-lived token expired and can no longer be refreshed; reconnect Threads",
                retryable=False,
            )
        issued = _parse_iso(str(token.get("issued_at") or ""))
        if issued and issued > _now() - timedelta(hours=24):
            raise ProviderAdapterError(
                "Threads token is near expiry but is not yet eligible for the long-token refresh window; reconnect Threads",
                retryable=False,
            )
        try:
            response = requests.get(
                f"{_THREADS_GRAPH}/refresh_access_token",
                params={"grant_type": "th_refresh_token", "access_token": access},
                timeout=_timeout(),
            )
            _checked(response, "refresh")
        except requests.RequestException as exc:
            raise _network("refresh", exc) from exc
        updated = response.json() or {}
        refreshed = str(updated.get("access_token") or "").strip()
        if not refreshed:
            raise ProviderAdapterError(
                "Threads token refresh did not return an access token",
                retryable=False,
            )
        token.update(updated)
        token["issued_at"] = _iso(_now())
        token["expires_at"] = _iso(
            _now() + timedelta(seconds=max(60, int(updated.get("expires_in") or 5184000)))
        )
        self._resave_token(user_id, record, token)
        return refreshed

    setattr(access_token_with_threads, "_threads_oauth_extension", True)
    setattr(access_token_with_threads, "_threads_oauth_original", original)
    SocialOAuthVault.access_token = access_token_with_threads
    return True


_service: ThreadsOAuthService | None = None


def _service_instance() -> ThreadsOAuthService:
    global _service
    if _service is None:
        _service = ThreadsOAuthService()
    return _service


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


@router.get("/oauth/threads/capability")
def threads_oauth_capability(request: Request):
    _member(request)
    cfg = _config()
    try:
        SocialOAuthVault._fernet()
        vault_ready = True
    except RuntimeError:
        vault_ready = False
    return {
        "provider": "threads",
        "configured": bool(cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"].startswith("https://") and vault_ready),
        "encrypted_vault_ready": vault_ready,
        "redirect_uri": cfg["redirect_uri"],
        "scopes": list(_REQUIRED_SCOPES),
        "publishing_adapter": "threads_graph",
        "tokens_exposed_to_browser": False,
    }


@router.get("/oauth/threads/start", include_in_schema=False)
def threads_oauth_start(space_id: str, request: Request):
    member = _member(request)
    try:
        url = _service_instance().authorization_url(user_id=member.user_id, space_id=space_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return RedirectResponse(url, status_code=303)


@router.get("/oauth/threads/callback", response_class=HTMLResponse, include_in_schema=False)
def threads_oauth_callback(
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
            "<!doctype html><title>Threads connection not completed</title>"
            "<h1>Threads connection not completed</h1>"
            f"<p>{message}</p>"
            "<p><a href='/command-center/social/connections'>Return to Social Connections</a></p>",
            status_code=400,
        )
    try:
        connection = _service_instance().complete(
            user_id=member.user_id,
            state=state,
            code=code,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ProviderAdapterError as exc:
        raise HTTPException(503 if exc.retryable else 400, str(exc)) from exc
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise HTTPException(400, f"Threads account connection failed: {exc}") from exc
    return HTMLResponse(
        "<!doctype html><title>Threads connected</title>"
        "<h1>Threads connected</h1>"
        f"<p>{escape(connection.account_label)} is authorised for ESP Social Management publishing.</p>"
        "<p>No OAuth token was exposed to this browser page.</p>"
        "<p><a href='/command-center/social/connections'>Return to Social Connections</a></p>"
    )


@router.post("/oauth/threads/disconnect")
def threads_oauth_disconnect(space_id: str, connection_id: str, request: Request):
    member = _member(request)
    try:
        _service_instance().disconnect(
            user_id=member.user_id,
            space_id=space_id,
            connection_id=connection_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Threads connection not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"disconnected": True, "connection_id": connection_id, "tokens_exposed": False}


install_threads_oauth_token_extension()


__all__ = [
    "ThreadsOAuthService",
    "ThreadsOAuthState",
    "install_threads_oauth_token_extension",
    "router",
    "threads_oauth_capability",
    "threads_oauth_callback",
    "threads_oauth_disconnect",
    "threads_oauth_start",
]

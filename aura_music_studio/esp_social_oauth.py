from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode
from uuid import uuid4

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .esp_niche import require_esp_social_member
from .esp_social_provider_adapters import ProviderAdapterError
from .request_context import current_user_id
from .social_management import ActivityEvent, SocialConnection, SocialHouseStore, utc_now

SocialOAuthProvider = Literal["tiktok", "instagram", "youtube"]

router = APIRouter(tags=["ESP Social OAuth"])

_TIKTOK_AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize/"
_TIKTOK_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
_TIKTOK_REVOKE = "https://open.tiktokapis.com/v2/oauth/revoke/"
_GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"
_YOUTUBE_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
_INSTAGRAM_AUTHORIZE = "https://www.instagram.com/oauth/authorize"
_INSTAGRAM_TOKEN = "https://api.instagram.com/oauth/access_token"
_INSTAGRAM_GRAPH = "https://graph.instagram.com"

_PROVIDER_SCOPES: dict[str, list[str]] = {
    "tiktok": ["user.info.basic", "video.publish"],
    "instagram": ["instagram_business_basic", "instagram_business_content_publish"],
    "youtube": [
        "openid",
        "email",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ],
}
_PROVIDER_ADAPTERS = {
    "tiktok": "tiktok_content_posting",
    "instagram": "instagram_graph",
    "youtube": "youtube_data_v3",
}
_OAUTH_REF = re.compile(r"^social-oauth://([A-Za-z0-9][A-Za-z0-9_-]{0,95})$")
_RETRYABLE_HTTP = {408, 425, 429}
_OAUTH_STATE_TTL = timedelta(minutes=15)
_OAUTH_STATE_RETENTION = timedelta(minutes=30)
_OAUTH_STATE_KEY_PREFIX = "sha256:"


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


def _oauth_state_key(state: str) -> str:
    """Return the non-reversible database key for an OAuth continuation bearer."""
    return _OAUTH_STATE_KEY_PREFIX + sha256(state.encode("utf-8")).hexdigest()


def _db_path() -> Path:
    configured = (
        os.getenv("AURA_SOCIAL_OAUTH_DB_PATH")
        or os.getenv("LSS_DB_PATH")
        or "data/live_sound_studio.sqlite3"
    ).strip()
    path = Path(configured).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _http_timeout() -> int:
    try:
        value = int(os.getenv("AURA_SOCIAL_OAUTH_HTTP_TIMEOUT", "25"))
    except ValueError:
        value = 25
    return max(5, min(value, 120))


def _provider_config(provider: SocialOAuthProvider) -> dict[str, str]:
    base = (os.getenv("LSS_PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
    callback = f"{base}/command-center/api/social/oauth/{provider}/callback"
    if provider == "tiktok":
        return {
            "client_id": (os.getenv("TIKTOK_OAUTH_CLIENT_KEY") or "").strip(),
            "client_secret": (os.getenv("TIKTOK_OAUTH_CLIENT_SECRET") or "").strip(),
            "redirect_uri": (os.getenv("TIKTOK_OAUTH_REDIRECT_URI") or callback).strip(),
        }
    if provider == "instagram":
        return {
            "client_id": (os.getenv("INSTAGRAM_OAUTH_APP_ID") or "").strip(),
            "client_secret": (os.getenv("INSTAGRAM_OAUTH_APP_SECRET") or "").strip(),
            "redirect_uri": (os.getenv("INSTAGRAM_OAUTH_REDIRECT_URI") or callback).strip(),
        }
    return {
        "client_id": (os.getenv("YOUTUBE_OAUTH_CLIENT_ID") or "").strip(),
        "client_secret": (os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET") or "").strip(),
        "redirect_uri": (os.getenv("YOUTUBE_OAUTH_REDIRECT_URI") or callback).strip(),
    }


def _checked_response(response: requests.Response, provider: str, action: str) -> requests.Response:
    if response.ok:
        return response
    status = int(response.status_code)
    retryable = status >= 500 or status in _RETRYABLE_HTTP
    # Do not include response bodies because OAuth providers may echo sensitive material.
    raise ProviderAdapterError(
        f"{provider} OAuth {action} failed with HTTP {status}",
        retryable=retryable,
    )


def _request_error(provider: str, action: str, exc: requests.RequestException) -> ProviderAdapterError:
    return ProviderAdapterError(
        f"{provider} OAuth {action} could not reach the provider",
        retryable=True,
    )


def oauth_credential_id(secret_ref: str | None) -> str | None:
    match = _OAUTH_REF.fullmatch((secret_ref or "").strip())
    return match.group(1) if match else None


def valid_social_oauth_ref(secret_ref: str | None) -> bool:
    return oauth_credential_id(secret_ref) is not None


class SocialOAuthVault:
    """Encrypted, tenant-bound OAuth token vault for privileged social publishing.

    Tokens never enter Social House JSON. Social House stores only a social-oauth:// ref.
    The worker resolves that ref while a tenant request context is active, so a credential
    belonging to one member cannot be opened from another member's queue.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or _db_path()
        self.ensure_schema()

    def _connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS esp_social_oauth_credentials (
                       user_id TEXT NOT NULL,
                       credential_id TEXT NOT NULL,
                       provider TEXT NOT NULL,
                       encrypted_token TEXT NOT NULL,
                       scopes_json TEXT NOT NULL DEFAULT '[]',
                       account_external_id TEXT,
                       account_label TEXT,
                       metadata_json TEXT NOT NULL DEFAULT '{}',
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL,
                       PRIMARY KEY(user_id, credential_id)
                   )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS esp_social_oauth_state (
                       state TEXT PRIMARY KEY,
                       user_id TEXT NOT NULL,
                       space_id TEXT NOT NULL,
                       provider TEXT NOT NULL,
                       connection_id TEXT NOT NULL,
                       credential_id TEXT NOT NULL,
                       scopes_json TEXT NOT NULL,
                       created_at TEXT NOT NULL
                   )"""
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_esp_social_oauth_state_user ON esp_social_oauth_state(user_id, created_at)"
            )

    @staticmethod
    def _fernet() -> Fernet:
        key = (os.getenv("AURA_SOCIAL_OAUTH_MASTER_KEY") or "").strip()
        if not key:
            raise RuntimeError("AURA_SOCIAL_OAUTH_MASTER_KEY is not configured")
        try:
            return Fernet(key.encode("ascii"))
        except Exception as exc:
            raise RuntimeError("AURA_SOCIAL_OAUTH_MASTER_KEY must be a valid Fernet key") from exc

    def diagnostics(self) -> dict:
        try:
            self._fernet()
            encrypted_vault_ready = True
        except RuntimeError:
            encrypted_vault_ready = False
        providers = {}
        for provider in ("tiktok", "instagram", "youtube"):
            cfg = _provider_config(provider)  # type: ignore[arg-type]
            providers[provider] = {
                "configured": bool(cfg["client_id"] and cfg["client_secret"]),
                "redirect_uri": cfg["redirect_uri"],
                "scopes": list(_PROVIDER_SCOPES[provider]),
            }
        return {
            "encrypted_vault_ready": encrypted_vault_ready,
            "providers": providers,
            "tokens_exposed_to_browser": False,
            "oauth_state_storage": "sha256_digest",
            "oauth_state_single_use": True,
        }

    def create_state(
        self,
        user_id: str,
        space_id: str,
        provider: SocialOAuthProvider,
    ) -> tuple[str, str, str]:
        self._fernet()
        state = secrets.token_urlsafe(48)
        state_key = _oauth_state_key(state)
        connection_id = f"conn_{uuid4().hex}"
        credential_id = f"socialcred_{uuid4().hex}"
        now = _now()
        with self._connect() as con:
            con.execute(
                "DELETE FROM esp_social_oauth_state WHERE created_at<?",
                (_iso(now - _OAUTH_STATE_RETENTION),),
            )
            con.execute(
                """INSERT INTO esp_social_oauth_state
                   (state,user_id,space_id,provider,connection_id,credential_id,scopes_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    state_key,
                    user_id,
                    space_id,
                    provider,
                    connection_id,
                    credential_id,
                    json.dumps(_PROVIDER_SCOPES[provider]),
                    _iso(now),
                ),
            )
        return state, connection_id, credential_id

    def consume_state(self, user_id: str, provider: SocialOAuthProvider, state: str) -> dict:
        clean_state = (state or "").strip()
        if not clean_state or len(clean_state) > 512:
            raise PermissionError("OAuth state is invalid or expired")
        state_key = _oauth_state_key(clean_state)
        now = _now()
        with self._connect() as con:
            # Acquire the SQLite write reservation before reading. Without this, two callbacks can
            # both SELECT the bearer before either DELETE executes and both appear successful.
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                """SELECT * FROM esp_social_oauth_state
                   WHERE state IN (?, ?) AND user_id=? AND provider=?""",
                (state_key, clean_state, user_id, provider),
            ).fetchone()
            if row:
                # Delete the exact stored key while the write reservation is held. The raw-state
                # fallback exists only so a callback created immediately before this deployment can
                # finish; every newly created state is digest-only at rest.
                con.execute("DELETE FROM esp_social_oauth_state WHERE state=?", (row["state"],))
        if not row:
            raise PermissionError("OAuth state is invalid or expired")
        created = _parse_iso(row["created_at"])
        if not created or created < now - _OAUTH_STATE_TTL:
            raise PermissionError("OAuth state is invalid or expired")
        return {
            "space_id": row["space_id"],
            "connection_id": row["connection_id"],
            "credential_id": row["credential_id"],
            "scopes": json.loads(row["scopes_json"] or "[]"),
        }

    def save(
        self,
        *,
        user_id: str,
        credential_id: str,
        provider: SocialOAuthProvider,
        token: dict,
        scopes: list[str],
        account_external_id: str,
        account_label: str,
        metadata: dict | None = None,
    ) -> None:
        encrypted = self._fernet().encrypt(
            json.dumps(token, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        now = _iso(_now())
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_social_oauth_credentials
                   (user_id,credential_id,provider,encrypted_token,scopes_json,account_external_id,
                    account_label,metadata_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,credential_id) DO UPDATE SET
                     provider=excluded.provider, encrypted_token=excluded.encrypted_token,
                     scopes_json=excluded.scopes_json, account_external_id=excluded.account_external_id,
                     account_label=excluded.account_label, metadata_json=excluded.metadata_json,
                     updated_at=excluded.updated_at""",
                (
                    user_id,
                    credential_id,
                    provider,
                    encrypted,
                    json.dumps(scopes),
                    account_external_id,
                    account_label,
                    json.dumps(metadata or {}),
                    now,
                    now,
                ),
            )

    def load(self, user_id: str, credential_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_social_oauth_credentials WHERE user_id=? AND credential_id=?",
                (user_id, credential_id),
            ).fetchone()
        if not row:
            return None
        try:
            token = json.loads(
                self._fernet()
                .decrypt(row["encrypted_token"].encode("ascii"))
                .decode("utf-8")
            )
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Social OAuth credential cannot be decrypted with this deployment key"
            ) from exc
        return {
            "credential_id": row["credential_id"],
            "provider": row["provider"],
            "token": token,
            "scopes": json.loads(row["scopes_json"] or "[]"),
            "account_external_id": row["account_external_id"],
            "account_label": row["account_label"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete(self, user_id: str, credential_id: str) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM esp_social_oauth_credentials WHERE user_id=? AND credential_id=?",
                (user_id, credential_id),
            )
        return cur.rowcount > 0

    def _resave_token(self, user_id: str, record: dict, token: dict) -> None:
        self.save(
            user_id=user_id,
            credential_id=record["credential_id"],
            provider=record["provider"],
            token=token,
            scopes=record["scopes"],
            account_external_id=record.get("account_external_id") or "",
            account_label=record.get("account_label") or "",
            metadata=record.get("metadata") or {},
        )

    def access_token(self, user_id: str, credential_id: str) -> str:
        record = self.load(user_id, credential_id)
        if not record:
            raise LookupError("Social OAuth credential does not exist for this member")
        token = dict(record["token"])
        provider = str(record["provider"])
        access = str(token.get("access_token") or "").strip()
        if not access:
            raise ProviderAdapterError(
                "Social OAuth credential has no access token; reconnect the account",
                retryable=False,
            )
        expiry = _parse_iso(str(token.get("expires_at") or ""))
        if not expiry or expiry > _now() + timedelta(minutes=5):
            return access
        timeout = _http_timeout()
        try:
            if provider == "tiktok":
                refresh = str(token.get("refresh_token") or "").strip()
                if not refresh:
                    raise ProviderAdapterError(
                        "TikTok access expired and has no refresh token; reconnect TikTok",
                        retryable=False,
                    )
                cfg = _provider_config("tiktok")
                response = requests.post(
                    _TIKTOK_TOKEN,
                    data={
                        "client_key": cfg["client_id"],
                        "client_secret": cfg["client_secret"],
                        "grant_type": "refresh_token",
                        "refresh_token": refresh,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=timeout,
                )
                _checked_response(response, "TikTok", "refresh")
                updated = response.json()
                if not updated.get("access_token"):
                    raise ProviderAdapterError(
                        "TikTok token refresh did not return an access token",
                        retryable=False,
                    )
                token.update(updated)
                token["refresh_token"] = str(updated.get("refresh_token") or refresh)
                token["expires_at"] = _iso(
                    _now() + timedelta(seconds=max(60, int(updated.get("expires_in") or 86400)))
                )
                if updated.get("refresh_expires_in"):
                    token["refresh_expires_at"] = _iso(
                        _now() + timedelta(seconds=int(updated["refresh_expires_in"]))
                    )
            elif provider == "youtube":
                refresh = str(token.get("refresh_token") or "").strip()
                if not refresh:
                    raise ProviderAdapterError(
                        "YouTube access expired and has no refresh token; reconnect YouTube",
                        retryable=False,
                    )
                cfg = _provider_config("youtube")
                response = requests.post(
                    _GOOGLE_TOKEN,
                    data={
                        "client_id": cfg["client_id"],
                        "client_secret": cfg["client_secret"],
                        "refresh_token": refresh,
                        "grant_type": "refresh_token",
                    },
                    timeout=timeout,
                )
                _checked_response(response, "YouTube", "refresh")
                updated = response.json()
                if not updated.get("access_token"):
                    raise ProviderAdapterError(
                        "YouTube token refresh did not return an access token",
                        retryable=False,
                    )
                token.update(updated)
                token["refresh_token"] = refresh
                token["expires_at"] = _iso(
                    _now() + timedelta(seconds=max(60, int(updated.get("expires_in") or 3600)))
                )
            elif provider == "instagram":
                issued = _parse_iso(str(token.get("issued_at") or ""))
                if issued and issued > _now() - timedelta(hours=24):
                    return access
                response = requests.get(
                    f"{_INSTAGRAM_GRAPH}/refresh_access_token",
                    params={"grant_type": "ig_refresh_token", "access_token": access},
                    timeout=timeout,
                )
                _checked_response(response, "Instagram", "refresh")
                updated = response.json()
                if not updated.get("access_token"):
                    raise ProviderAdapterError(
                        "Instagram token refresh did not return an access token",
                        retryable=False,
                    )
                token.update(updated)
                token["issued_at"] = _iso(_now())
                token["expires_at"] = _iso(
                    _now()
                    + timedelta(seconds=max(60, int(updated.get("expires_in") or 5184000)))
                )
            else:
                raise ProviderAdapterError("Unsupported Social OAuth provider", retryable=False)
        except requests.RequestException as exc:
            raise _request_error(provider.title(), "refresh", exc) from exc
        self._resave_token(user_id, record, token)
        return str(token.get("access_token") or "")


vault: SocialOAuthVault | None = None


def _vault() -> SocialOAuthVault:
    global vault
    if vault is None or vault.path != _db_path():
        vault = SocialOAuthVault()
    return vault


def resolve_social_oauth_token(secret_ref: str | None, *, user_id: str | None = None) -> str:
    credential_id = oauth_credential_id(secret_ref)
    if not credential_id:
        raise ValueError("Social OAuth token reference must use social-oauth://<credential-id>")
    member_id = user_id or current_user_id()
    if not member_id:
        raise PermissionError("A tenant context is required to resolve Social OAuth credentials")
    return _vault().access_token(member_id, credential_id)


class SocialOAuthService:
    def __init__(self, token_vault: SocialOAuthVault | None = None):
        self.vault = token_vault or _vault()
        self.timeout = _http_timeout()

    @staticmethod
    def _require_config(provider: SocialOAuthProvider) -> dict[str, str]:
        cfg = _provider_config(provider)
        if not cfg["client_id"] or not cfg["client_secret"]:
            raise RuntimeError(
                f"{provider.title()} OAuth application credentials are not configured"
            )
        if provider == "tiktok" and not cfg["redirect_uri"].startswith("https://"):
            raise RuntimeError("TikTok Web OAuth redirect URI must use HTTPS")
        return cfg

    def authorization_url(
        self,
        *,
        user_id: str,
        space_id: str,
        provider: SocialOAuthProvider,
    ) -> str:
        cfg = self._require_config(provider)
        SocialHouseStore().load(space_id)
        state, _connection_id, _credential_id = self.vault.create_state(
            user_id, space_id, provider
        )
        scopes = _PROVIDER_SCOPES[provider]
        if provider == "tiktok":
            return _TIKTOK_AUTHORIZE + "?" + urlencode(
                {
                    "client_key": cfg["client_id"],
                    "scope": ",".join(scopes),
                    "response_type": "code",
                    "redirect_uri": cfg["redirect_uri"],
                    "state": state,
                }
            )
        if provider == "instagram":
            return _INSTAGRAM_AUTHORIZE + "?" + urlencode(
                {
                    "client_id": cfg["client_id"],
                    "redirect_uri": cfg["redirect_uri"],
                    "scope": ",".join(scopes),
                    "response_type": "code",
                    "enable_fb_login": "0",
                    "force_authentication": "1",
                    "state": state,
                }
            )
        return _GOOGLE_AUTHORIZE + "?" + urlencode(
            {
                "client_id": cfg["client_id"],
                "redirect_uri": cfg["redirect_uri"],
                "response_type": "code",
                "scope": " ".join(scopes),
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "state": state,
            }
        )

    @staticmethod
    def _scope_list(value, fallback: list[str]) -> list[str]:
        if isinstance(value, list):
            values = [str(item).strip() for item in value]
        else:
            raw = str(value or "").replace(" ", ",")
            values = [item.strip() for item in raw.split(",")]
        result = [item for item in values if item]
        return list(dict.fromkeys(result)) or list(fallback)

    def _exchange_tiktok(
        self,
        cfg: dict[str, str],
        code: str,
        requested: list[str],
    ) -> tuple[dict, list[str], str, str, dict]:
        try:
            response = requests.post(
                _TIKTOK_TOKEN,
                data={
                    "client_key": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": cfg["redirect_uri"],
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
            _checked_response(response, "TikTok", "code exchange")
        except requests.RequestException as exc:
            raise _request_error("TikTok", "code exchange", exc) from exc
        token = response.json()
        access = str(token.get("access_token") or "").strip()
        open_id = str(token.get("open_id") or "").strip()
        if not access or not open_id:
            raise RuntimeError("TikTok OAuth response did not include access_token and open_id")
        token["expires_at"] = _iso(
            _now() + timedelta(seconds=max(60, int(token.get("expires_in") or 86400)))
        )
        if token.get("refresh_expires_in"):
            token["refresh_expires_at"] = _iso(
                _now() + timedelta(seconds=int(token["refresh_expires_in"]))
            )
        scopes = self._scope_list(token.get("scope"), requested)
        label = f"TikTok {open_id[-8:]}"
        try:
            info = requests.get(
                "https://open.tiktokapis.com/v2/user/info/",
                params={"fields": "open_id,display_name"},
                headers={"Authorization": f"Bearer {access}"},
                timeout=self.timeout,
            )
            if info.ok:
                user = ((info.json() or {}).get("data") or {}).get("user") or {}
                label = str(user.get("display_name") or label)[:200]
        except requests.RequestException:
            pass
        return token, scopes, open_id, label, {"oauth_flow": "tiktok_login_kit"}

    def _exchange_youtube(
        self,
        cfg: dict[str, str],
        code: str,
        requested: list[str],
    ) -> tuple[dict, list[str], str, str, dict]:
        try:
            response = requests.post(
                _GOOGLE_TOKEN,
                data={
                    "code": code,
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "redirect_uri": cfg["redirect_uri"],
                    "grant_type": "authorization_code",
                },
                timeout=self.timeout,
            )
            _checked_response(response, "YouTube", "code exchange")
            token = response.json()
            access = str(token.get("access_token") or "").strip()
            if not access:
                raise RuntimeError("YouTube OAuth response did not include an access token")
            token["expires_at"] = _iso(
                _now() + timedelta(seconds=max(60, int(token.get("expires_in") or 3600)))
            )
            scopes = self._scope_list(token.get("scope"), requested)
            channels = requests.get(
                _YOUTUBE_CHANNELS,
                params={"part": "snippet", "mine": "true", "maxResults": 1},
                headers={"Authorization": f"Bearer {access}"},
                timeout=self.timeout,
            )
            _checked_response(channels, "YouTube", "channel verification")
        except requests.RequestException as exc:
            raise _request_error("YouTube", "connection", exc) from exc
        items = (channels.json() or {}).get("items") or []
        if not items:
            raise RuntimeError("The authorised Google account does not expose a YouTube channel")
        channel = items[0]
        channel_id = str(channel.get("id") or "").strip()
        title = str((channel.get("snippet") or {}).get("title") or channel_id).strip()[:200]
        if not channel_id:
            raise RuntimeError("YouTube channel identity could not be verified")
        return token, scopes, channel_id, title, {"oauth_flow": "google_youtube"}

    def _exchange_instagram(
        self,
        cfg: dict[str, str],
        code: str,
        requested: list[str],
    ) -> tuple[dict, list[str], str, str, dict]:
        try:
            short = requests.post(
                _INSTAGRAM_TOKEN,
                data={
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "grant_type": "authorization_code",
                    "redirect_uri": cfg["redirect_uri"],
                    "code": code,
                },
                timeout=self.timeout,
            )
            _checked_response(short, "Instagram", "code exchange")
            short_token = short.json()
            short_access = str(short_token.get("access_token") or "").strip()
            if not short_access:
                raise RuntimeError("Instagram OAuth response did not include an access token")
            long_response = requests.get(
                f"{_INSTAGRAM_GRAPH}/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": cfg["client_secret"],
                    "access_token": short_access,
                },
                timeout=self.timeout,
            )
            _checked_response(long_response, "Instagram", "long-lived token exchange")
            token = long_response.json()
            access = str(token.get("access_token") or "").strip()
            if not access:
                raise RuntimeError(
                    "Instagram long-lived token exchange did not return an access token"
                )
            token["issued_at"] = _iso(_now())
            token["expires_at"] = _iso(
                _now()
                + timedelta(seconds=max(60, int(token.get("expires_in") or 5184000)))
            )
            profile = requests.get(
                f"{_INSTAGRAM_GRAPH}/me",
                params={"fields": "id,username,account_type", "access_token": access},
                timeout=self.timeout,
            )
            _checked_response(profile, "Instagram", "profile verification")
        except requests.RequestException as exc:
            raise _request_error("Instagram", "connection", exc) from exc
        data = profile.json() or {}
        ig_id = str(data.get("id") or short_token.get("user_id") or "").strip()
        username = str(data.get("username") or ig_id).strip()[:200]
        account_type = str(data.get("account_type") or "").upper()
        if not ig_id:
            raise RuntimeError("Instagram account identity could not be verified")
        if account_type and account_type not in {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}:
            raise PermissionError(
                "Instagram publishing requires a Professional Business or Creator account"
            )
        return token, list(requested), ig_id, username, {
            "oauth_flow": "instagram_login",
            "instagram_account_type": account_type,
            "instagram_graph_host": "graph.instagram.com",
        }

    def complete(
        self,
        *,
        user_id: str,
        provider: SocialOAuthProvider,
        state: str,
        code: str,
    ) -> SocialConnection:
        pending = self.vault.consume_state(user_id, provider, state)
        cfg = self._require_config(provider)
        clean_code = (code or "").strip()
        if not clean_code or len(clean_code) > 4096:
            raise ValueError("OAuth authorization code is missing or invalid")
        requested = [str(value) for value in pending["scopes"]]
        if provider == "tiktok":
            token, scopes, external_id, label, provider_meta = self._exchange_tiktok(
                cfg, clean_code, requested
            )
        elif provider == "instagram":
            token, scopes, external_id, label, provider_meta = self._exchange_instagram(
                cfg, clean_code, requested
            )
        else:
            token, scopes, external_id, label, provider_meta = self._exchange_youtube(
                cfg, clean_code, requested
            )
        missing = [scope for scope in requested if scope not in scopes]
        if missing:
            raise PermissionError(
                f"Required publishing permission was not granted: {', '.join(missing)}"
            )
        credential_id = pending["credential_id"]
        self.vault.save(
            user_id=user_id,
            credential_id=credential_id,
            provider=provider,
            token=token,
            scopes=scopes,
            account_external_id=external_id,
            account_label=label,
            metadata=provider_meta,
        )
        connection = SocialConnection(
            id=pending["connection_id"],
            platform=provider,
            account_label=label,
            account_external_id=external_id,
            state="connected",
            supports_auto_publish=True,
            supports_analytics=False,
            supports_inbox=False,
            token_secret_ref=f"social-oauth://{credential_id}",
            metadata={
                "publishing_adapter": _PROVIDER_ADAPTERS[provider],
                "publishing_adapter_active": True,
                "oauth_provider": provider,
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
                detail=f"{provider}:{external_id}",
            )
        )
        store.save(house)
        return connection

    def disconnect(
        self,
        *,
        user_id: str,
        space_id: str,
        connection_id: str,
        expected_provider: SocialOAuthProvider | None = None,
    ) -> bool:
        store = SocialHouseStore()
        house = store.load(space_id)
        connection = next((item for item in house.connections if item.id == connection_id), None)
        if connection is None:
            raise KeyError(connection_id)
        if expected_provider is not None and connection.platform != expected_provider:
            raise ValueError("OAuth provider does not match the selected social connection")
        credential_id = oauth_credential_id(connection.token_secret_ref)
        if not credential_id:
            raise ValueError("Connection is not backed by a member OAuth credential")
        record = self.vault.load(user_id, credential_id)
        if record and str(record["provider"]) != connection.platform:
            raise PermissionError("Stored OAuth credential provider does not match the connection")
        if record:
            access = str(record["token"].get("access_token") or "")
            try:
                if record["provider"] == "tiktok" and access:
                    cfg = _provider_config("tiktok")
                    requests.post(
                        _TIKTOK_REVOKE,
                        data={
                            "client_key": cfg["client_id"],
                            "client_secret": cfg["client_secret"],
                            "token": access,
                        },
                        timeout=self.timeout,
                    )
                elif record["provider"] == "youtube" and access:
                    requests.post(_GOOGLE_REVOKE, params={"token": access}, timeout=self.timeout)
            except requests.RequestException:
                pass
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
                detail=connection.platform,
            )
        )
        store.save(house)
        return True


service: SocialOAuthService | None = None


def _service() -> SocialOAuthService:
    global service
    if service is None or service.vault.path != _db_path():
        service = SocialOAuthService(_vault())
    return service


def _member(request: Request):
    member, _membership, _profile = require_esp_social_member(request)
    return member


@router.get("/oauth/providers")
def oauth_providers(request: Request):
    _member(request)
    return _vault().diagnostics()


@router.get("/oauth/{provider}/start", include_in_schema=False)
def oauth_start(provider: str, space_id: str, request: Request):
    member = _member(request)
    if provider not in _PROVIDER_SCOPES:
        raise HTTPException(404, "Unsupported social OAuth provider")
    try:
        url = _service().authorization_url(
            user_id=member.user_id,
            space_id=space_id,
            provider=provider,  # type: ignore[arg-type]
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return RedirectResponse(url, status_code=303)


@router.get(
    "/oauth/{provider}/callback",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def oauth_callback(
    provider: str,
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    error_description: str = "",
):
    member = _member(request)
    if provider not in _PROVIDER_SCOPES:
        raise HTTPException(404, "Unsupported social OAuth provider")
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
            provider=provider,  # type: ignore[arg-type]
            state=state,
            code=code,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ProviderAdapterError as exc:
        status = 503 if exc.retryable else 400
        raise HTTPException(status, str(exc)) from exc
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise HTTPException(400, f"Social account connection failed: {exc}") from exc
    return HTMLResponse(
        "<!doctype html><title>Social account connected</title>"
        f"<h1>{escape(connection.platform.title())} connected</h1>"
        f"<p>{escape(connection.account_label)} is authorised for ESP Social Management.</p>"
        "<p>No OAuth token was exposed to this browser page.</p>"
        "<p><a href='/command-center/social'>Return to ESP Social Management</a></p>"
    )


@router.post("/oauth/{provider}/disconnect")
def oauth_disconnect(provider: str, space_id: str, connection_id: str, request: Request):
    member = _member(request)
    if provider not in _PROVIDER_SCOPES:
        raise HTTPException(404, "Unsupported social OAuth provider")
    try:
        _service().disconnect(
            user_id=member.user_id,
            space_id=space_id,
            connection_id=connection_id,
            expected_provider=provider,  # type: ignore[arg-type]
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


__all__ = [
    "SocialOAuthService",
    "SocialOAuthVault",
    "oauth_credential_id",
    "resolve_social_oauth_token",
    "router",
    "valid_social_oauth_ref",
]

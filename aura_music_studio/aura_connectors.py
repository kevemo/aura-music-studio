from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode
from uuid import uuid4

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_chat_store import AuraChatStore

router = APIRouter(tags=["Aura Connectors"])
store = AuraChatStore()
_INSTALLED = False
_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"
_GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_SCOPES = {
    "drive": "https://www.googleapis.com/auth/drive.readonly",
    "calendar": "https://www.googleapis.com/auth/calendar.readonly",
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
}
_BASE_SCOPES = ("openid", "email")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    clean = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        result = datetime.fromisoformat(clean)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _service_list(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if isinstance(raw, str):
        rows = [item.strip().lower() for item in raw.split(",")]
    else:
        rows = [str(item).strip().lower() for item in (raw or [])]
    result = []
    for item in rows:
        if item in _GOOGLE_SCOPES and item not in result:
            result.append(item)
    return result or ["drive", "calendar", "gmail"]


class ConnectorVault:
    def __init__(self, chat_store: AuraChatStore | None = None):
        self.chat_store = chat_store or store
        self.ensure_schema()

    def ensure_schema(self):
        with self.chat_store._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS aura_connectors (
                       user_id TEXT NOT NULL,
                       provider TEXT NOT NULL,
                       encrypted_token TEXT NOT NULL,
                       services_json TEXT NOT NULL DEFAULT '[]',
                       account_label TEXT,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL,
                       PRIMARY KEY(user_id, provider)
                   )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS aura_connector_oauth_state (
                       state TEXT PRIMARY KEY,
                       user_id TEXT NOT NULL,
                       provider TEXT NOT NULL,
                       services_json TEXT NOT NULL,
                       created_at TEXT NOT NULL
                   )"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_aura_connector_state_user ON aura_connector_oauth_state(user_id,created_at)")

    @staticmethod
    def _fernet() -> Fernet:
        key = (os.getenv("AURA_CONNECTOR_MASTER_KEY") or "").strip()
        if not key:
            raise RuntimeError("AURA_CONNECTOR_MASTER_KEY is not configured")
        try:
            return Fernet(key.encode("ascii"))
        except Exception as exc:
            raise RuntimeError("AURA_CONNECTOR_MASTER_KEY must be a valid Fernet key") from exc

    def diagnostics(self) -> dict:
        try:
            self._fernet()
            encryption_ready = True
        except Exception:
            encryption_ready = False
        return {
            "encrypted_vault_ready": encryption_ready,
            "google_oauth_configured": bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID") and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")),
            "read_only_default": True,
        }

    def create_state(self, user_id: str, provider: str, services: list[str]) -> str:
        state = uuid4().hex + uuid4().hex
        now = _iso(_now())
        with self.chat_store._connect() as con:
            con.execute("DELETE FROM aura_connector_oauth_state WHERE created_at<?", (_iso(_now() - timedelta(minutes=20)),))
            con.execute(
                "INSERT INTO aura_connector_oauth_state(state,user_id,provider,services_json,created_at) VALUES (?,?,?,?,?)",
                (state, user_id, provider, json.dumps(services), now),
            )
        return state

    def consume_state(self, user_id: str, state: str, provider: str) -> list[str]:
        with self.chat_store._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_connector_oauth_state WHERE state=? AND user_id=? AND provider=?",
                (state, user_id, provider),
            ).fetchone()
            if row:
                con.execute("DELETE FROM aura_connector_oauth_state WHERE state=?", (state,))
        if not row:
            raise PermissionError("OAuth state is invalid or belongs to another session")
        created = _parse_iso(row["created_at"])
        if not created or created < _now() - timedelta(minutes=15):
            raise PermissionError("OAuth state has expired")
        try:
            return _service_list(json.loads(row["services_json"]))
        except Exception:
            return _service_list(None)

    def save(self, user_id: str, provider: str, token: dict, services: list[str], account_label: str | None = None):
        encrypted = self._fernet().encrypt(json.dumps(token, ensure_ascii=False).encode("utf-8")).decode("ascii")
        now = _iso(_now())
        with self.chat_store._connect() as con:
            con.execute(
                """INSERT INTO aura_connectors(user_id,provider,encrypted_token,services_json,account_label,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,provider) DO UPDATE SET encrypted_token=excluded.encrypted_token,
                   services_json=excluded.services_json,account_label=excluded.account_label,updated_at=excluded.updated_at""",
                (user_id, provider, encrypted, json.dumps(services), account_label, now, now),
            )

    def load(self, user_id: str, provider: str) -> dict | None:
        with self.chat_store._connect() as con:
            row = con.execute("SELECT * FROM aura_connectors WHERE user_id=? AND provider=?", (user_id, provider)).fetchone()
        if not row:
            return None
        try:
            token = json.loads(self._fernet().decrypt(row["encrypted_token"].encode("ascii")).decode("utf-8"))
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Connector credential could not be decrypted with this deployment key") from exc
        return {
            "provider": provider,
            "token": token,
            "services": _service_list(json.loads(row["services_json"])),
            "account_label": row["account_label"],
            "updated_at": row["updated_at"],
        }

    def status(self, user_id: str) -> list[dict]:
        with self.chat_store._connect() as con:
            rows = con.execute("SELECT provider,services_json,account_label,updated_at FROM aura_connectors WHERE user_id=? ORDER BY provider", (user_id,)).fetchall()
        result = []
        for row in rows:
            try:
                services = _service_list(json.loads(row["services_json"]))
            except Exception:
                services = []
            result.append({
                "provider": row["provider"],
                "services": services,
                "account_label": row["account_label"],
                "updated_at": row["updated_at"],
                "encrypted_at_rest": True,
                "read_only": True,
            })
        return result

    def delete(self, user_id: str, provider: str) -> bool:
        with self.chat_store._connect() as con:
            cur = con.execute("DELETE FROM aura_connectors WHERE user_id=? AND provider=?", (user_id, provider))
        return cur.rowcount > 0


vault = ConnectorVault(store)


class GoogleConnector:
    def __init__(self, connector_vault: ConnectorVault | None = None):
        self.vault = connector_vault or vault
        self.timeout = max(5, min(int(os.getenv("AURA_CONNECTOR_HTTP_TIMEOUT", "25")), 120))

    @staticmethod
    def config() -> dict:
        client_id = (os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
        client_secret = (os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
        redirect = (os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or "").strip()
        if not redirect:
            base = (os.getenv("LSS_PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
            redirect = base + "/aura-intelligence/connectors/google/callback"
        return {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect}

    def authorization_url(self, user_id: str, services: list[str]) -> str:
        cfg = self.config()
        if not cfg["client_id"] or not cfg["client_secret"]:
            raise RuntimeError("Google OAuth client credentials are not configured")
        # Verify encryption before starting OAuth so the callback never obtains a token it cannot store safely.
        self.vault._fernet()
        selected = _service_list(services)
        state = self.vault.create_state(user_id, "google", selected)
        scopes = list(_BASE_SCOPES) + [_GOOGLE_SCOPES[item] for item in selected]
        params = {
            "client_id": cfg["client_id"],
            "redirect_uri": cfg["redirect_uri"],
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return _GOOGLE_AUTH + "?" + urlencode(params)

    def exchange(self, user_id: str, state: str, code: str) -> dict:
        services = self.vault.consume_state(user_id, state, "google")
        cfg = self.config()
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
        response.raise_for_status()
        data = response.json()
        access = str(data.get("access_token") or "")
        if not access:
            raise RuntimeError("Google OAuth token response did not include an access token")
        data["expires_at"] = _iso(_now() + timedelta(seconds=max(60, int(data.get("expires_in") or 3600))))
        account_label = None
        try:
            info = requests.get(_GOOGLE_USERINFO, headers={"Authorization": f"Bearer {access}"}, timeout=self.timeout)
            if info.ok:
                account_label = str((info.json() or {}).get("email") or "")[:320] or None
        except Exception:
            pass
        self.vault.save(user_id, "google", data, services, account_label)
        return {"provider": "google", "services": services, "account_label": account_label, "read_only": True}

    def _token(self, user_id: str, required_service: str) -> str:
        record = self.vault.load(user_id, "google")
        if not record:
            raise PermissionError("Connect Google to Aura first")
        if required_service not in record["services"]:
            raise PermissionError(f"Google {required_service.title()} access was not granted to Aura")
        token = dict(record["token"])
        expiry = _parse_iso(str(token.get("expires_at") or ""))
        if expiry and expiry > _now() + timedelta(seconds=90):
            return str(token.get("access_token") or "")
        refresh = str(token.get("refresh_token") or "")
        if not refresh:
            raise PermissionError("Google access expired and no refresh token is available; reconnect Google")
        cfg = self.config()
        response = requests.post(
            _GOOGLE_TOKEN,
            data={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        updated = response.json()
        token.update(updated)
        token["refresh_token"] = refresh
        token["expires_at"] = _iso(_now() + timedelta(seconds=max(60, int(updated.get("expires_in") or 3600))))
        self.vault.save(user_id, "google", token, record["services"], record.get("account_label"))
        return str(token.get("access_token") or "")

    def _get(self, user_id: str, service: str, url: str, *, params: dict | None = None) -> dict:
        token = self._token(user_id, service)
        response = requests.get(url, params=params or {}, headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
        if response.status_code == 401:
            # Force one refresh retry by expiring the stored access token locally.
            record = self.vault.load(user_id, "google")
            if record:
                data = dict(record["token"])
                data["expires_at"] = _iso(_now() - timedelta(minutes=5))
                self.vault.save(user_id, "google", data, record["services"], record.get("account_label"))
                token = self._token(user_id, service)
                response = requests.get(url, params=params or {}, headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def drive_search(self, user_id: str, query: str, limit: int = 10) -> dict:
        clean = " ".join((query or "").split()).strip()
        if not clean:
            raise ValueError("Drive search query is required")
        escaped = clean.replace("\\", "\\\\").replace("'", "\\'")[:500]
        data = self._get(
            user_id,
            "drive",
            "https://www.googleapis.com/drive/v3/files",
            params={
                "q": f"trashed = false and fullText contains '{escaped}'",
                "pageSize": max(1, min(int(limit), 20)),
                "orderBy": "modifiedTime desc",
                "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink,starred)",
            },
        )
        return {
            "query": clean,
            "files": [
                {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "mime_type": row.get("mimeType"),
                    "modified_time": row.get("modifiedTime"),
                    "size": row.get("size"),
                    "web_view_link": row.get("webViewLink"),
                    "starred": bool(row.get("starred")),
                }
                for row in (data.get("files") or [])[:20]
            ],
            "read_only": True,
        }

    def calendar_events(
        self,
        user_id: str,
        *,
        time_min: str | None = None,
        time_max: str | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> dict:
        start = _parse_iso(time_min) if time_min else _now()
        end = _parse_iso(time_max) if time_max else start + timedelta(days=7)
        if not start or not end or end <= start:
            raise ValueError("Calendar time range is invalid")
        params: dict[str, Any] = {
            "timeMin": _iso(start),
            "timeMax": _iso(end),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(int(limit), 50)),
        }
        if query:
            params["q"] = " ".join(query.split())[:500]
        data = self._get(
            user_id,
            "calendar",
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            params=params,
        )
        events = []
        for row in (data.get("items") or [])[:50]:
            events.append({
                "id": row.get("id"),
                "summary": row.get("summary") or "(untitled)",
                "start": (row.get("start") or {}).get("dateTime") or (row.get("start") or {}).get("date"),
                "end": (row.get("end") or {}).get("dateTime") or (row.get("end") or {}).get("date"),
                "location": row.get("location"),
                "status": row.get("status"),
                "html_link": row.get("htmlLink"),
            })
        return {"time_min": _iso(start), "time_max": _iso(end), "events": events, "read_only": True}

    def gmail_search(self, user_id: str, query: str, limit: int = 10) -> dict:
        clean = " ".join((query or "").split()).strip()
        if not clean:
            raise ValueError("Gmail search query is required")
        listed = self._get(
            user_id,
            "gmail",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": clean[:1000], "maxResults": max(1, min(int(limit), 15))},
        )
        rows = []
        for item in (listed.get("messages") or [])[:15]:
            message_id = item.get("id")
            if not message_id:
                continue
            data = self._get(
                user_id,
                "gmail",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(str(message_id), safe='')}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "To", "Date"],
                },
            )
            headers = {
                str(row.get("name") or "").lower(): row.get("value")
                for row in ((data.get("payload") or {}).get("headers") or [])
            }
            rows.append({
                "id": data.get("id"),
                "thread_id": data.get("threadId"),
                "subject": headers.get("subject") or "(no subject)",
                "from": headers.get("from"),
                "to": headers.get("to"),
                "date": headers.get("date"),
                "snippet": str(data.get("snippet") or "")[:1200],
                "label_ids": (data.get("labelIds") or [])[:20],
            })
        return {"query": clean, "messages": rows, "read_only": True, "message_bodies_included": False}

    def revoke(self, user_id: str) -> bool:
        record = self.vault.load(user_id, "google")
        if not record:
            return False
        token = str(record["token"].get("refresh_token") or record["token"].get("access_token") or "")
        if token:
            try:
                requests.post(_GOOGLE_REVOKE, params={"token": token}, timeout=self.timeout)
            except Exception:
                pass
        return self.vault.delete(user_id, "google")


google = GoogleConnector(vault)


CONNECTOR_SPECS = [
    tools.ToolSpec(
        "connector_status",
        "List private services the signed-in member has explicitly connected to Aura. Never returns access/refresh tokens.",
        {},
    ),
    tools.ToolSpec(
        "google_drive_search",
        "Search the member's explicitly connected Google Drive read-only and return bounded file metadata.",
        {"query": "Plain-language Drive search text.", "limit": "1-20, default 10."},
    ),
    tools.ToolSpec(
        "google_calendar_events",
        "Read events from the member's connected primary Google Calendar. Defaults to the next 7 days.",
        {"time_min": "Optional RFC3339/ISO timestamp.", "time_max": "Optional RFC3339/ISO timestamp.", "query": "Optional event text search.", "limit": "1-50."},
    ),
    tools.ToolSpec(
        "gmail_search",
        "Search the member's connected Gmail using Gmail search syntax and return metadata/snippets only.",
        {"query": "Gmail query such as from:x is:unread or plain search text.", "limit": "1-15."},
    ),
]


def _connector_related(text: str) -> bool:
    lower = (text or "").lower()
    return any(term in lower for term in ("google drive", "my drive", "gmail", "my email", "inbox", "my calendar", "google calendar", "connected services", "connectors"))


def _after_for(text: str, phrases: tuple[str, ...]) -> str | None:
    lower = text.lower()
    for phrase in phrases:
        index = lower.find(phrase)
        if index >= 0:
            value = text[index + len(phrase):].strip(" :,-.?")
            if value:
                return value
    return None


def install_aura_connector_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    for spec in CONNECTOR_SPECS:
        if spec.name not in {item.name for item in tools.TOOL_SPECS}:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec

    original_execute = tools.AuraToolRegistry.execute
    original_direct = core._direct_tool_plan
    original_needs = core._needs_model_tool_router

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name not in {"connector_status", "google_drive_search", "google_calendar_events", "gmail_search"}:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        if call.name == "connector_status":
            return {"connectors": vault.status(self.member.user_id), "tokens_exposed": False}
        args = dict(call.arguments or {})
        if call.name == "google_drive_search":
            return google.drive_search(self.member.user_id, str(args.get("query") or ""), int(args.get("limit") or 10))
        if call.name == "google_calendar_events":
            return google.calendar_events(
                self.member.user_id,
                time_min=str(args.get("time_min") or "") or None,
                time_max=str(args.get("time_max") or "") or None,
                query=str(args.get("query") or "") or None,
                limit=int(args.get("limit") or 20),
            )
        return google.gmail_search(self.member.user_id, str(args.get("query") or ""), int(args.get("limit") or 10))

    def direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool):
        prior = original_direct(text, pinned_project, web_enabled)
        if prior is not None:
            return prior
        lower = text.lower()
        if any(x in lower for x in ("show my connected services", "show connectors", "connector status")):
            return tools.ToolPlan(calls=[tools.ToolCall(name="connector_status", arguments={})])
        drive_query = _after_for(text, ("search my drive for", "search google drive for", "find in my drive", "find in google drive"))
        if drive_query:
            return tools.ToolPlan(calls=[tools.ToolCall(name="google_drive_search", arguments={"query": drive_query, "limit": 10})])
        gmail_query = _after_for(text, ("search my email for", "search gmail for", "find in my email", "find in gmail"))
        if gmail_query:
            return tools.ToolPlan(calls=[tools.ToolCall(name="gmail_search", arguments={"query": gmail_query, "limit": 10})])
        if any(x in lower for x in ("what's on my calendar", "what is on my calendar", "show my calendar", "my calendar this week", "my calendar for the next week")):
            return tools.ToolPlan(calls=[tools.ToolCall(name="google_calendar_events", arguments={"limit": 20})])
        return None

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        if tools_enabled and _connector_related(text):
            return True
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._direct_tool_plan = direct_tool_plan
    core._needs_model_tool_router = needs_model_tool_router
    _INSTALLED = True


@router.get("/aura-intelligence/api/connectors")
def connector_status(request: Request):
    member = _member(request)
    return {"connectors": vault.status(member.user_id), "runtime": vault.diagnostics(), "tokens_exposed": False}


@router.get("/aura-intelligence/connectors/google/start")
def google_start(request: Request, services: str = "drive,calendar,gmail"):
    member = _member(request)
    try:
        url = google.authorization_url(member.user_id, _service_list(services))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return RedirectResponse(url, status_code=302)


@router.get("/aura-intelligence/connectors/google/callback")
def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    member = _member(request)
    if error:
        return RedirectResponse("/aura-intelligence?connector=google&status=declined", status_code=302)
    if not state or not code:
        raise HTTPException(400, "Google OAuth callback is missing state or authorization code")
    try:
        google.exchange(member.user_id, state, code)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(502, "Google OAuth token exchange failed") from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return RedirectResponse("/aura-intelligence?connector=google&status=connected", status_code=302)


@router.delete("/aura-intelligence/api/connectors/google")
def google_disconnect(request: Request):
    member = _member(request)
    try:
        deleted = google.revoke(member.user_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"disconnected": bool(deleted), "provider": "google"}


__all__ = [
    "router",
    "ConnectorVault",
    "GoogleConnector",
    "vault",
    "google",
    "install_aura_connector_tools",
    "_service_list",
]

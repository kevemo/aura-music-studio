from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import esp_shop_automation as base

router = APIRouter(tags=["ESP Shop Provider Runtime"])

OAUTH_TTL_MINUTES = 15
RUNTIME_DB_PATH: str | None = None
_SECRET_REF = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s]{1,480}$", re.I)

ACTION_CAPABILITIES = {
    "purchase_shipping_label": {"shopify": "shipping_labels", "shipstation": "labels", "shippo": "labels"},
    "buy_label": {"shopify": "shipping_labels", "shipstation": "labels", "shippo": "labels"},
    "update_inventory": {"shopify": "inventory", "tiktok_shop": "inventory"},
    "set_inventory": {"shopify": "inventory", "tiktok_shop": "inventory"},
    "fulfill_order": {"shopify": "fulfillment", "tiktok_shop": "fulfillment"},
    "update_fulfillment": {"shopify": "fulfillment", "tiktok_shop": "fulfillment"},
    "update_tracking": {"tiktok_shop": "tracking", "shipstation": "tracking", "shippo": "tracking"},
    "create_return": {"shopify": "returns", "shipstation": "returns", "shippo": "returns"},
    "refund": {"shopify": "returns"},
}

SENSITIVE_SPEND_ACTIONS = {"purchase_shipping_label", "buy_label", "refund", "purchase", "fulfill_order"}
INVENTORY_ACTIONS = {"update_inventory", "set_inventory", "adjust_inventory"}
FULFILLMENT_ACTIONS = {"fulfill_order", "update_fulfillment", "mark_fulfilled"}
NOTIFICATION_ACTIONS = {"notify_customer", "send_tracking_notification", "send_order_notification"}
LABEL_ACTIONS = {"purchase_shipping_label", "buy_label"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _redact(value):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            name = str(key).lower()
            if any(word in name for word in ("token", "secret", "password", "authorization", "credential")):
                clean[key] = "[redacted]"
            else:
                clean[key] = _redact(item)
        return clean
    if isinstance(value, list):
        return [_redact(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:4000]
    return value


def configure_runtime_db(db_path: str) -> None:
    global RUNTIME_DB_PATH
    RUNTIME_DB_PATH = str(db_path)


class ShopProviderAdapter(Protocol):
    provider: str

    def authorization_url(self, connection: dict, *, state: str, callback_url: str) -> str: ...

    def exchange_oauth_code(self, connection: dict, *, code: str, callback_url: str) -> dict: ...

    def execute(self, action: dict, connection: dict, *, secret_ref: str) -> dict: ...

    def diagnostics(self) -> dict: ...


PROVIDER_ADAPTERS: dict[str, ShopProviderAdapter] = {}


def register_provider_adapter(provider: str, adapter: ShopProviderAdapter) -> None:
    key = str(provider or "").strip().lower()
    if key not in base.PROVIDERS:
        raise ValueError("Unsupported Shop provider")
    if str(getattr(adapter, "provider", key)).strip().lower() != key:
        raise ValueError("Provider adapter identity does not match registration key")
    PROVIDER_ADAPTERS[key] = adapter


def clear_provider_adapters() -> None:
    PROVIDER_ADAPTERS.clear()


class ProviderRuntimeStore:
    """OAuth/execution bridge that preserves the Shop foundation's fail-closed safety model.

    Raw provider tokens are owned by an external secret system/adapter. This database stores
    only a secret reference and verified execution receipts.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self.base = base.ShopAutomationStore(self.db_path)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_shop_oauth_states (
                    id TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    state_sha256 TEXT NOT NULL UNIQUE,
                    callback_url TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(connection_id) REFERENCES esp_shop_connections(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shop_oauth_state_connection
                    ON esp_shop_oauth_states(connection_id,expires_at);

                CREATE TABLE IF NOT EXISTS esp_shop_provider_credentials (
                    connection_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    secret_ref TEXT NOT NULL,
                    external_account_ref TEXT NOT NULL DEFAULT '',
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    verified_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(connection_id) REFERENCES esp_shop_connections(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shop_provider_credentials_user
                    ON esp_shop_provider_credentials(user_id,provider);

                CREATE TABLE IF NOT EXISTS esp_shop_provider_receipts (
                    id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    execution_ref TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(action_id) REFERENCES esp_shop_action_queue(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_shop_provider_receipts_action
                    ON esp_shop_provider_receipts(action_id,created_at DESC);
                """
            )

    @staticmethod
    def _adapter(provider: str) -> ShopProviderAdapter:
        adapter = PROVIDER_ADAPTERS.get(provider)
        if adapter is None:
            raise RuntimeError(f"{provider} provider adapter is not configured")
        return adapter

    def credential_state(self, connection_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                "SELECT provider,external_account_ref,scopes_json,verified_at,updated_at FROM esp_shop_provider_credentials WHERE connection_id=? AND user_id=?",
                (connection_id, user_id),
            ).fetchone()
        if not row:
            return {"present": False, "provider": None, "verified_at": None, "secret_ref_exposed": False}
        item = dict(row)
        try:
            item["scopes"] = json.loads(item.pop("scopes_json") or "[]")
        except Exception:
            item["scopes"] = []
        item["present"] = True
        item["secret_ref_exposed"] = False
        return item

    def begin_oauth(self, connection_id: str, user_id: str, *, callback_url: str) -> dict:
        connection = self.base.connection(connection_id, user_id)
        if connection["status"] not in {"pending_oauth", "needs_reauth", "error", "revoked"}:
            raise ValueError("This provider connection is not waiting for OAuth")
        adapter = self._adapter(connection["provider"])
        state = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(minutes=OAUTH_TTL_MINUTES)
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_shop_oauth_states
                   (id,connection_id,user_id,provider,state_sha256,callback_url,expires_at,consumed_at,created_at)
                   VALUES (?,?,?,?,?,?,?,NULL,?)""",
                (uuid4().hex, connection_id, user_id, connection["provider"], _state_hash(state), callback_url, expires.isoformat(), now),
            )
        authorization_url = str(adapter.authorization_url(connection, state=state, callback_url=callback_url) or "").strip()
        parsed = urlparse(authorization_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("Provider adapter did not return a valid HTTPS authorization URL")
        return {
            "provider": connection["provider"],
            "connection_id": connection_id,
            "authorization_url": authorization_url,
            "expires_at": expires.isoformat(),
            "state_stored_as_hash": True,
            "raw_oauth_token_stored": False,
        }

    def complete_oauth(self, provider: str, *, state: str, code: str) -> dict:
        provider = str(provider or "").strip().lower()
        if provider not in base.PROVIDERS:
            raise ValueError("Unsupported Shop provider")
        digest = _state_hash(state)
        with self._connect() as con:
            row = con.execute(
                """SELECT * FROM esp_shop_oauth_states
                   WHERE state_sha256=? AND provider=? AND consumed_at IS NULL""",
                (digest, provider),
            ).fetchone()
            if not row:
                raise PermissionError("OAuth state is invalid or already consumed")
            if _parse_time(row["expires_at"]) < datetime.now(timezone.utc):
                con.execute("UPDATE esp_shop_oauth_states SET consumed_at=? WHERE id=?", (_now(), row["id"]))
                raise PermissionError("OAuth state has expired")
            con.execute("UPDATE esp_shop_oauth_states SET consumed_at=? WHERE id=?", (_now(), row["id"]))
        connection = self.base.connection(row["connection_id"], row["user_id"])
        adapter = self._adapter(provider)
        verified = adapter.exchange_oauth_code(connection, code=code, callback_url=row["callback_url"])
        if not isinstance(verified, dict):
            raise RuntimeError("Provider adapter returned an invalid OAuth verification result")
        secret_ref = str(verified.get("secret_ref") or "").strip()
        if not _SECRET_REF.fullmatch(secret_ref):
            raise RuntimeError("Provider adapter must return a secret-system reference, never a raw OAuth token")
        external_ref = str(verified.get("external_account_ref") or connection.get("external_account_ref") or "").strip()[:240]
        allowed_scopes = base.PROVIDERS[provider]
        scopes = sorted({str(value).strip() for value in (verified.get("scopes") or []) if str(value).strip() in allowed_scopes})
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_shop_provider_credentials
                   (connection_id,user_id,provider,secret_ref,external_account_ref,scopes_json,verified_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(connection_id) DO UPDATE SET secret_ref=excluded.secret_ref,
                     external_account_ref=excluded.external_account_ref,scopes_json=excluded.scopes_json,
                     verified_at=excluded.verified_at,updated_at=excluded.updated_at""",
                (connection["id"], row["user_id"], provider, secret_ref, external_ref, json.dumps(scopes), now, now),
            )
            if external_ref:
                con.execute(
                    "UPDATE esp_shop_connections SET external_account_ref=? WHERE id=? AND user_id=?",
                    (external_ref, connection["id"], row["user_id"]),
                )
        updated = self.base.set_connection_status(
            connection["id"], row["user_id"], "provider_oauth_callback",
            base.ConnectionStatusUpdate(status="connected", scopes=scopes, error_code=""),
        )
        return {
            "connection": updated,
            "credential": self.credential_state(connection["id"], row["user_id"]),
            "oauth_verified": True,
            "raw_oauth_token_stored": False,
        }

    def _credential(self, connection_id: str, user_id: str):
        with self._connect() as con:
            return con.execute(
                "SELECT * FROM esp_shop_provider_credentials WHERE connection_id=? AND user_id=?",
                (connection_id, user_id),
            ).fetchone()

    def _connected_provider(self, user_id: str, provider: str) -> tuple[dict, sqlite3.Row]:
        connections = [row for row in self.base.connections(user_id) if row["provider"] == provider and row["status"] == "connected"]
        for connection in connections:
            credential = self._credential(connection["id"], user_id)
            if credential:
                return connection, credential
        raise RuntimeError(f"No verified connected {provider} account is available")

    @staticmethod
    def _required_capability(action: dict) -> str | None:
        mapping = ACTION_CAPABILITIES.get(action.get("action_type") or "") or {}
        return mapping.get(action.get("provider") or "")

    def _recheck_policy(self, action: dict) -> None:
        policy = self.base.policy(action["user_id"])
        action_type = action["action_type"]
        approved = action["status"] == "approved"
        spend = int(action.get("estimated_spend_minor") or 0)
        needs_approval = False
        if action_type in SENSITIVE_SPEND_ACTIONS and policy["require_purchase_confirmation"]:
            needs_approval = True
        if spend > int(policy["preapproved_spend_minor"] or 0):
            needs_approval = True
        if needs_approval and not approved:
            with self._connect() as con:
                con.execute(
                    "UPDATE esp_shop_action_queue SET status='awaiting_approval',updated_at=? WHERE id=? AND user_id=? AND status='prepared'",
                    (_now(), action["id"], action["user_id"]),
                )
                self.base._audit(con, action["user_id"], "provider_runtime", "execution_blocked_for_approval", "action", action["id"])
            raise PermissionError("Current Shop safety policy requires explicit approval before this action executes")
        if action_type in INVENTORY_ACTIONS and not policy["allow_inventory_writes"]:
            raise PermissionError("Inventory writes are disabled in the creator's Shop safety policy")
        if action_type in FULFILLMENT_ACTIONS and not policy["allow_fulfillment_writes"]:
            raise PermissionError("Fulfillment writes are disabled in the creator's Shop safety policy")
        if action_type in NOTIFICATION_ACTIONS and not policy["allow_customer_notifications"]:
            raise PermissionError("Customer notifications are disabled in the creator's Shop safety policy")
        if action_type in LABEL_ACTIONS:
            try:
                quantity = int((action.get("payload") or {}).get("quantity") or 1)
            except (TypeError, ValueError):
                quantity = 1
            if quantity > 1 and not policy["allow_bulk_labels"]:
                raise PermissionError("Bulk shipping-label purchases are disabled in the creator's Shop safety policy")

    def execute_action(self, action_id: str, user_id: str, *, actor: str) -> dict:
        action = self.base.action(action_id, user_id)
        if action["status"] == "awaiting_approval":
            raise PermissionError("Approve this Shop action before provider execution")
        if action["status"] not in {"prepared", "approved"}:
            raise ValueError("This Shop action is not eligible for provider execution")
        self._recheck_policy(action)
        connection, credential = self._connected_provider(user_id, action["provider"])
        capability = self._required_capability(action)
        if capability and capability not in set(connection.get("scopes") or []):
            raise PermissionError(f"Connected provider account is missing the required {capability} capability")
        adapter = self._adapter(action["provider"])
        try:
            receipt = adapter.execute(action, connection, secret_ref=credential["secret_ref"])
        except Exception as exc:
            now = _now()
            safe_error = str(exc)[:500]
            with self._connect() as con:
                con.execute(
                    "UPDATE esp_shop_action_queue SET status='failed',updated_at=? WHERE id=? AND user_id=?",
                    (now, action_id, user_id),
                )
                con.execute(
                    """INSERT INTO esp_shop_provider_receipts(id,action_id,user_id,provider,execution_ref,status,metadata_json,created_at)
                       VALUES (?,?,?,?,?,'failed',?,?)""",
                    (uuid4().hex, action_id, user_id, action["provider"], "", json.dumps({"error": safe_error}), now),
                )
                self.base._audit(con, user_id, actor, "provider_execution_failed", "action", action_id, {"provider": action["provider"]})
            raise RuntimeError("Provider execution failed; the action was not marked executed") from exc
        if not isinstance(receipt, dict) or receipt.get("success") is not True:
            raise RuntimeError("Provider adapter did not return a verified successful execution receipt")
        execution_ref = str(receipt.get("execution_ref") or "").strip()[:500]
        if not execution_ref:
            raise RuntimeError("Provider execution receipt is missing its external execution reference")
        metadata = _redact(receipt.get("metadata") or {})
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_shop_action_queue SET status='executed',provider_execution_ref=?,executed_at=?,updated_at=?
                   WHERE id=? AND user_id=?""",
                (execution_ref, now, now, action_id, user_id),
            )
            con.execute(
                """INSERT INTO esp_shop_provider_receipts(id,action_id,user_id,provider,execution_ref,status,metadata_json,created_at)
                   VALUES (?,?,?,?,?,'executed',?,?)""",
                (uuid4().hex, action_id, user_id, action["provider"], execution_ref, json.dumps(metadata, sort_keys=True), now),
            )
            self.base._audit(con, user_id, actor, "provider_execution_confirmed", "action", action_id, {"provider": action["provider"], "execution_ref": execution_ref})
        return {
            "action": self.base.action(action_id, user_id),
            "receipt": {"execution_ref": execution_ref, "status": "executed", "metadata": metadata},
            "provider_execution_confirmed": True,
        }

    def diagnostics(self, user_id: str) -> dict:
        connections = []
        for connection in self.base.connections(user_id):
            connections.append({
                "id": connection["id"],
                "provider": connection["provider"],
                "status": connection["status"],
                "scopes": connection.get("scopes") or [],
                "credential": self.credential_state(connection["id"], user_id),
                "adapter_configured": connection["provider"] in PROVIDER_ADAPTERS,
            })
        configured = {}
        for provider in base.PROVIDERS:
            adapter = PROVIDER_ADAPTERS.get(provider)
            configured[provider] = _redact(adapter.diagnostics()) if adapter else {"configured": False}
        return {
            "connections": connections,
            "providers": configured,
            "oauth_state_stored_as_hash": True,
            "raw_oauth_tokens_in_database": False,
            "execution_requires_verified_receipt": True,
            "safety_policy_rechecked_at_execution": True,
        }


def _authenticated_runtime(request: Request):
    store, member, plan_id, tier = base._store(request)
    base._require_automation(tier)
    return ProviderRuntimeStore(store.db_path), member, plan_id, tier


def _callback_url(request: Request, provider: str) -> str:
    return str(request.base_url).rstrip("/") + f"/command-center/shop-automation/oauth/{provider}/callback"


@router.get("/command-center/api/shop-automation/runtime")
def runtime_diagnostics_api(request: Request):
    runtime, member, _plan_id, _tier = _authenticated_runtime(request)
    return runtime.diagnostics(member.user_id)


@router.post("/command-center/api/shop-automation/connections/{connection_id}/oauth/start")
def oauth_start_api(connection_id: str, request: Request):
    runtime, member, _plan_id, _tier = _authenticated_runtime(request)
    try:
        connection = runtime.base.connection(connection_id, member.user_id)
        return runtime.begin_oauth(connection_id, member.user_id, callback_url=_callback_url(request, connection["provider"]))
    except KeyError as exc:
        raise HTTPException(404, "Shop connection not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/command-center/shop-automation/oauth/{provider}/callback", include_in_schema=False)
def oauth_callback(provider: str, state: str, code: str):
    if not RUNTIME_DB_PATH:
        raise HTTPException(503, "Shop provider runtime database is not configured")
    runtime = ProviderRuntimeStore(RUNTIME_DB_PATH)
    try:
        result = runtime.complete_oauth(provider, state=state, code=code)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    connection_id = result["connection"]["id"]
    return RedirectResponse(f"/command-center/shop-automation?oauth=connected&connection={connection_id}", status_code=303)


@router.post("/command-center/api/shop-automation/actions/{action_id}/execute")
def execute_action_api(action_id: str, request: Request):
    runtime, member, _plan_id, _tier = _authenticated_runtime(request)
    try:
        return runtime.execute_action(action_id, member.user_id, actor=member.user_id)
    except KeyError as exc:
        raise HTTPException(404, "Shop action not found") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


__all__ = [
    "router", "ProviderRuntimeStore", "ShopProviderAdapter", "register_provider_adapter",
    "clear_provider_adapters", "configure_runtime_db", "PROVIDER_ADAPTERS",
]

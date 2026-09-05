from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member

router = APIRouter(tags=["ESP Shop Creator Automation"])

Provider = Literal["shopify", "tiktok_shop", "shipstation", "shippo"]
ConnectionStatus = Literal["pending_oauth", "connected", "needs_reauth", "error", "revoked"]
WorkflowStatus = Literal["draft", "active", "paused", "archived"]
ActionStatus = Literal["prepared", "awaiting_approval", "approved", "executed", "failed", "cancelled"]

PROVIDERS = {
    "shopify": {"catalog", "inventory", "orders", "fulfillment", "returns", "shipping_labels", "webhooks"},
    "tiktok_shop": {"catalog", "inventory", "orders", "fulfillment", "tracking", "affiliate"},
    "shipstation": {"orders", "rates", "labels", "tracking", "returns"},
    "shippo": {"orders", "rates", "labels", "tracking", "returns"},
}

SHOP_TIERS = {
    "free": {"id": "training_only", "automation": False, "unlimited": False},
    "base": {"id": "basic", "automation": True, "unlimited": False},
    "pro": {"id": "unlimited", "automation": True, "unlimited": True},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _creator_context(request: Request):
    member, membership = require_esp_hub_member(request)
    role = "owner" if membership.get("status") == "owner" else (membership.get("roles") or "").lower()
    if role not in {"creator", "both", "owner"}:
        raise HTTPException(403, "ESP Creator or Owner access is required")
    plan_id = str(member.user.get("plan_id") or "free").lower()
    tier = SHOP_TIERS.get(plan_id, SHOP_TIERS["free"])
    return member, membership, role == "owner", plan_id, tier


def _require_automation(tier: dict) -> None:
    if not tier.get("automation"):
        raise HTTPException(403, "ESP Shop automation requires the £4.99 Basic or £9.99 Pro membership tier")


class ConnectionCreate(BaseModel):
    provider: Provider
    account_label: str = Field(default="", max_length=160)
    external_account_ref: str = Field(default="", max_length=240)
    scopes: list[str] = Field(default_factory=list, max_length=30)


class ConnectionStatusUpdate(BaseModel):
    status: ConnectionStatus
    scopes: list[str] = Field(default_factory=list, max_length=30)
    error_code: str = Field(default="", max_length=120)


class SafetyPolicyUpdate(BaseModel):
    require_purchase_confirmation: bool = True
    preapproved_spend_minor: int = Field(default=0, ge=0, le=500000)
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    allow_bulk_labels: bool = False
    allow_inventory_writes: bool = False
    allow_fulfillment_writes: bool = False
    allow_customer_notifications: bool = False


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=3, max_length=180)
    trigger_type: str = Field(min_length=2, max_length=100)
    conditions: list[dict] = Field(default_factory=list, max_length=20)
    actions: list[dict] = Field(default_factory=list, max_length=20)
    status: WorkflowStatus = "draft"


class ActionPrepare(BaseModel):
    provider: Provider
    action_type: str = Field(min_length=2, max_length=100)
    external_object_ref: str = Field(default="", max_length=240)
    payload: dict = Field(default_factory=dict)
    estimated_spend_minor: int = Field(default=0, ge=0, le=500000)
    currency: str = Field(default="GBP", min_length=3, max_length=3)


class ActionDecision(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=1000)


class ShopAutomationStore:
    """Fail-closed ESP Creator commerce orchestration.

    This store records OAuth/provider state, workflow intent and approval decisions. It does
    not store raw OAuth tokens and never marks an external action executed without a provider
    adapter later supplying verified execution evidence.
    """

    def __init__(self, db_path):
        self.db_path = db_path
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
                CREATE TABLE IF NOT EXISTS esp_shop_connections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    account_label TEXT NOT NULL DEFAULT '',
                    external_account_ref TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending_oauth',
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    error_code TEXT NOT NULL DEFAULT '',
                    connected_at TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id,provider,external_account_ref),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_shop_connections_user
                    ON esp_shop_connections(user_id,status,provider);

                CREATE TABLE IF NOT EXISTS esp_shop_safety_policies (
                    user_id TEXT PRIMARY KEY,
                    require_purchase_confirmation INTEGER NOT NULL DEFAULT 1,
                    preapproved_spend_minor INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'GBP',
                    allow_bulk_labels INTEGER NOT NULL DEFAULT 0,
                    allow_inventory_writes INTEGER NOT NULL DEFAULT 0,
                    allow_fulfillment_writes INTEGER NOT NULL DEFAULT 0,
                    allow_customer_notifications INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_shop_workflows (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    actions_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_shop_workflows_user
                    ON esp_shop_workflows(user_id,status,updated_at DESC);

                CREATE TABLE IF NOT EXISTS esp_shop_action_queue (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    external_object_ref TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    estimated_spend_minor INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'GBP',
                    status TEXT NOT NULL DEFAULT 'prepared',
                    approval_note TEXT NOT NULL DEFAULT '',
                    provider_execution_ref TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    executed_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_shop_actions_user
                    ON esp_shop_action_queue(user_id,status,created_at DESC);

                CREATE TABLE IF NOT EXISTS esp_shop_audit (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_esp_shop_audit_user
                    ON esp_shop_audit(user_id,created_at DESC);
                """
            )

    def _audit(self, con, user_id: str, actor: str, action: str, subject_type: str, subject_id: str, metadata: dict | None = None):
        con.execute(
            """INSERT INTO esp_shop_audit(id,user_id,actor_user_id,action,subject_type,subject_id,metadata_json,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (uuid4().hex, user_id, actor, action[:100], subject_type[:80], subject_id, json.dumps(metadata or {}, sort_keys=True), _now()),
        )

    def policy(self, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_shop_safety_policies WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {
                "user_id": user_id, "require_purchase_confirmation": True,
                "preapproved_spend_minor": 0, "currency": "GBP", "allow_bulk_labels": False,
                "allow_inventory_writes": False, "allow_fulfillment_writes": False,
                "allow_customer_notifications": False,
            }
        item = dict(row)
        for key in ("require_purchase_confirmation", "allow_bulk_labels", "allow_inventory_writes", "allow_fulfillment_writes", "allow_customer_notifications"):
            item[key] = bool(item[key])
        return item

    def save_policy(self, user_id: str, actor: str, body: SafetyPolicyUpdate) -> dict:
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_shop_safety_policies
                   (user_id,require_purchase_confirmation,preapproved_spend_minor,currency,allow_bulk_labels,
                    allow_inventory_writes,allow_fulfillment_writes,allow_customer_notifications,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     require_purchase_confirmation=excluded.require_purchase_confirmation,
                     preapproved_spend_minor=excluded.preapproved_spend_minor,currency=excluded.currency,
                     allow_bulk_labels=excluded.allow_bulk_labels,allow_inventory_writes=excluded.allow_inventory_writes,
                     allow_fulfillment_writes=excluded.allow_fulfillment_writes,
                     allow_customer_notifications=excluded.allow_customer_notifications,updated_at=excluded.updated_at""",
                (
                    user_id, int(body.require_purchase_confirmation), body.preapproved_spend_minor, body.currency.upper(),
                    int(body.allow_bulk_labels), int(body.allow_inventory_writes), int(body.allow_fulfillment_writes),
                    int(body.allow_customer_notifications), now,
                ),
            )
            self._audit(con, user_id, actor, "safety_policy_updated", "policy", user_id, {"spend_minor": body.preapproved_spend_minor})
        return self.policy(user_id)

    def add_connection(self, user_id: str, actor: str, body: ConnectionCreate) -> dict:
        allowed = PROVIDERS[body.provider]
        scopes = sorted({str(value).strip() for value in body.scopes if str(value).strip() in allowed})
        row_id, now = uuid4().hex, _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_shop_connections
                   (id,user_id,provider,account_label,external_account_ref,status,scopes_json,error_code,connected_at,updated_at)
                   VALUES (?,?,?,?,?,'pending_oauth',?,'',NULL,?)""",
                (row_id, user_id, body.provider, _clean(body.account_label, 160), _clean(body.external_account_ref, 240), json.dumps(scopes), now),
            )
            self._audit(con, user_id, actor, "connection_created", "connection", row_id, {"provider": body.provider})
        return self.connection(row_id, user_id)

    def connection(self, connection_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_shop_connections WHERE id=? AND user_id=?", (connection_id, user_id)).fetchone()
        if not row:
            raise KeyError(connection_id)
        item = dict(row)
        item["scopes"] = json.loads(item.pop("scopes_json") or "[]")
        item["raw_token_stored"] = False
        return item

    def connections(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT id FROM esp_shop_connections WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [self.connection(row["id"], user_id) for row in rows]

    def set_connection_status(self, connection_id: str, user_id: str, actor: str, body: ConnectionStatusUpdate) -> dict:
        current = self.connection(connection_id, user_id)
        allowed = PROVIDERS[current["provider"]]
        scopes = sorted({str(value).strip() for value in body.scopes if str(value).strip() in allowed})
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_shop_connections SET status=?,scopes_json=?,error_code=?,connected_at=?,updated_at=?
                   WHERE id=? AND user_id=?""",
                (body.status, json.dumps(scopes), _clean(body.error_code, 120), now if body.status == "connected" else current.get("connected_at"), now, connection_id, user_id),
            )
            self._audit(con, user_id, actor, "connection_status_updated", "connection", connection_id, {"status": body.status})
        return self.connection(connection_id, user_id)

    def create_workflow(self, user_id: str, actor: str, body: WorkflowCreate) -> dict:
        if not body.actions:
            raise ValueError("A Shop workflow requires at least one action")
        row_id, now = uuid4().hex, _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_shop_workflows(id,user_id,name,trigger_type,conditions_json,actions_json,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (row_id, user_id, _clean(body.name, 180), _clean(body.trigger_type, 100), json.dumps(body.conditions), json.dumps(body.actions), body.status, now, now),
            )
            self._audit(con, user_id, actor, "workflow_created", "workflow", row_id, {"status": body.status})
        return self.workflow(row_id, user_id)

    def workflow(self, workflow_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_shop_workflows WHERE id=? AND user_id=?", (workflow_id, user_id)).fetchone()
        if not row:
            raise KeyError(workflow_id)
        item = dict(row)
        item["conditions"] = json.loads(item.pop("conditions_json") or "[]")
        item["actions"] = json.loads(item.pop("actions_json") or "[]")
        return item

    def workflows(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT id FROM esp_shop_workflows WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [self.workflow(row["id"], user_id) for row in rows]

    def prepare_action(self, user_id: str, actor: str, body: ActionPrepare) -> dict:
        policy = self.policy(user_id)
        action_type = _clean(body.action_type, 100)
        spend = int(body.estimated_spend_minor)
        sensitive_write = action_type in {"purchase_shipping_label", "buy_label", "refund", "purchase", "fulfill_order"}
        requires_confirmation = bool(policy["require_purchase_confirmation"] and sensitive_write)
        if spend > int(policy["preapproved_spend_minor"] or 0):
            requires_confirmation = True
        status = "awaiting_approval" if requires_confirmation else "prepared"
        row_id, now = uuid4().hex, _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_shop_action_queue
                   (id,user_id,provider,action_type,external_object_ref,payload_json,estimated_spend_minor,currency,status,
                    approval_note,provider_execution_ref,created_at,approved_at,executed_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'','',?,NULL,NULL,?)""",
                (row_id, user_id, body.provider, action_type, _clean(body.external_object_ref, 240), json.dumps(body.payload, sort_keys=True), spend, body.currency.upper(), status, now, now),
            )
            self._audit(con, user_id, actor, "action_prepared", "action", row_id, {"provider": body.provider, "status": status, "spend_minor": spend})
        return self.action(row_id, user_id)

    def action(self, action_id: str, user_id: str) -> dict:
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_shop_action_queue WHERE id=? AND user_id=?", (action_id, user_id)).fetchone()
        if not row:
            raise KeyError(action_id)
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        item["provider_execution_confirmed"] = bool(item.get("provider_execution_ref") and item.get("status") == "executed")
        return item

    def actions(self, user_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT id FROM esp_shop_action_queue WHERE user_id=? ORDER BY created_at DESC LIMIT 200", (user_id,)).fetchall()
        return [self.action(row["id"], user_id) for row in rows]

    def decide_action(self, action_id: str, user_id: str, actor: str, body: ActionDecision) -> dict:
        current = self.action(action_id, user_id)
        if current["status"] not in {"awaiting_approval", "prepared"}:
            raise ValueError("This action can no longer be approved or cancelled")
        status = "approved" if body.approve else "cancelled"
        now = _now()
        with self._connect() as con:
            con.execute(
                """UPDATE esp_shop_action_queue SET status=?,approval_note=?,approved_at=?,updated_at=?
                   WHERE id=? AND user_id=?""",
                (status, _clean(body.note, 1000), now if body.approve else None, now, action_id, user_id),
            )
            self._audit(con, user_id, actor, "action_approved" if body.approve else "action_cancelled", "action", action_id)
        return self.action(action_id, user_id)

    def dashboard(self, user_id: str, plan_id: str) -> dict:
        tier = SHOP_TIERS.get(plan_id, SHOP_TIERS["free"])
        return {
            "shop_tier": tier["id"],
            "automation_enabled": tier["automation"],
            "unlimited_eligible_workflows": tier["unlimited"],
            "connections": self.connections(user_id),
            "policy": self.policy(user_id),
            "workflows": self.workflows(user_id),
            "actions": self.actions(user_id),
            "provider_capabilities": {key: sorted(value) for key, value in PROVIDERS.items()},
            "external_execution_adapter_configured": False,
            "raw_oauth_tokens_returned": False,
            "esp_creator_role_required": True,
        }


def _store(request: Request) -> tuple[ShopAutomationStore, object, str, dict]:
    member, _membership, _owner, plan_id, tier = _creator_context(request)
    return ShopAutomationStore(member.accounts.db_path), member, plan_id, tier


@router.get("/command-center/api/shop-automation")
def shop_automation_dashboard_api(request: Request):
    store, member, plan_id, _tier = _store(request)
    return store.dashboard(member.user_id, plan_id)


@router.put("/command-center/api/shop-automation/policy")
def shop_policy_api(body: SafetyPolicyUpdate, request: Request):
    store, member, _plan_id, tier = _store(request)
    _require_automation(tier)
    return {"policy": store.save_policy(member.user_id, member.user_id, body)}


@router.post("/command-center/api/shop-automation/connections")
def shop_connection_create_api(body: ConnectionCreate, request: Request):
    store, member, _plan_id, tier = _store(request)
    _require_automation(tier)
    return {"connection": store.add_connection(member.user_id, member.user_id, body), "oauth_token_exchange": "external_adapter_required"}


@router.patch("/command-center/api/shop-automation/connections/{connection_id}")
def shop_connection_status_api(connection_id: str, body: ConnectionStatusUpdate, request: Request):
    store, member, _plan_id, tier = _store(request)
    _require_automation(tier)
    try:
        return {"connection": store.set_connection_status(connection_id, member.user_id, member.user_id, body)}
    except KeyError as exc:
        raise HTTPException(404, "Shop connection not found") from exc


@router.post("/command-center/api/shop-automation/workflows")
def shop_workflow_create_api(body: WorkflowCreate, request: Request):
    store, member, _plan_id, tier = _store(request)
    _require_automation(tier)
    try:
        return {"workflow": store.create_workflow(member.user_id, member.user_id, body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/command-center/api/shop-automation/actions")
def shop_action_prepare_api(body: ActionPrepare, request: Request):
    store, member, _plan_id, tier = _store(request)
    _require_automation(tier)
    return {"action": store.prepare_action(member.user_id, member.user_id, body), "executed": False}


@router.post("/command-center/api/shop-automation/actions/{action_id}/decision")
def shop_action_decision_api(action_id: str, body: ActionDecision, request: Request):
    store, member, _plan_id, tier = _store(request)
    _require_automation(tier)
    try:
        return {"action": store.decide_action(action_id, member.user_id, member.user_id, body), "executed": False}
    except KeyError as exc:
        raise HTTPException(404, "Shop action not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


CSS = """
:root{--line:#ffffff1f;--muted:#c9bfd3;--gold:#efc66b;--purple:#a26fff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#42185d,transparent 30%),#07050d;color:#fff;font-family:Inter,system-ui,sans-serif}.wrap{width:min(1180px,calc(100% - 28px));margin:auto;padding:34px 0 60px}.top,.row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.eyebrow{color:var(--gold);font-weight:900;letter-spacing:.14em;text-transform:uppercase;font-size:.72rem}h1{font-size:clamp(2.5rem,7vw,5rem);letter-spacing:-.05em;margin:.15em 0}.muted{color:var(--muted);line-height:1.5}.card{border:1px solid var(--line);border-radius:18px;padding:16px;background:#14101deb;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.metric{border:1px solid var(--line);border-radius:12px;padding:10px;background:#ffffff06}.metric b{display:block;font-size:1.15rem}.btn{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#ffffff09;color:#fff;text-decoration:none;font-weight:800}.primary{border:0;background:linear-gradient(110deg,var(--gold),var(--purple));color:#160d1d}@media(max-width:760px){.grid{grid-template-columns:1fr 1fr}}@media(max-width:480px){.grid{grid-template-columns:1fr}}
"""


@router.get("/command-center/shop-automation", response_class=HTMLResponse, include_in_schema=False)
def shop_automation_page(request: Request):
    store, member, plan_id, tier = _store(request)
    data = store.dashboard(member.user_id, plan_id)
    connection_count = len(data["connections"])
    workflow_count = len(data["workflows"])
    approval_count = sum(1 for row in data["actions"] if row["status"] == "awaiting_approval")
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'>"
        f"<title>ESP Shop Automation</title><style>{CSS}</style></head><body><main class='wrap'><div class='top'><div>"
        "<div class='eyebrow'>Elevate Souls Productions · Shop Creator</div><h1>Shop Automation</h1>"
        "<p class='muted'>ESP Creator-only commerce orchestration for authorised Shopify, TikTok Shop and shipping-provider workflows. External writes remain fail-closed until an official provider adapter confirms execution.</p></div>"
        "<div><a class='btn' href='/command-center/commerce'>Commerce</a><a class='btn primary' href='/command-center'>ESP Hub</a></div></div>"
        f"<section class='grid'><div class='metric'><span class='muted'>Shop tier</span><b>{escape(data['shop_tier'].replace('_',' ').title())}</b></div>"
        f"<div class='metric'><span class='muted'>Connections</span><b>{connection_count}</b></div><div class='metric'><span class='muted'>Workflows</span><b>{workflow_count}</b></div>"
        f"<div class='metric'><span class='muted'>Awaiting approval</span><b>{approval_count}</b></div></section>"
        f"<section class='card'><h2>Safety boundary</h2><p class='muted'>Automation enabled: <b>{'Yes' if tier['automation'] else 'No — training/view only'}</b><br>Unlimited eligible workflows: <b>{'Yes' if tier['unlimited'] else 'No'}</b><br>Raw OAuth tokens returned to this interface: <b>No</b><br>Provider execution adapter configured by this foundation: <b>No</b></p></section>"
        "<section class='card'><h2>Supported adapter targets</h2><p class='muted'>Shopify · TikTok Shop · ShipStation · Shippo. Connections, scopes, spend policy, workflows and approval queue are persisted now; real provider OAuth/execution is activated only through separately configured official adapters.</p></section>"
        "</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "ShopAutomationStore", "SHOP_TIERS", "PROVIDERS"]

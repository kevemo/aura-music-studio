from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .credit_wallet import CreditWalletStore
from .plans import get_plan
from .subscriptions import SubscriptionLedger

router = APIRouter(prefix="/billing/stripe", tags=["Stripe Billing"])

_STRIPE_API = "https://api.stripe.com"
_WEBHOOK_TOLERANCE_SECONDS = 300


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_base_url(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw.startswith("https://") and not raw.startswith("http://localhost") and not raw.startswith("http://127.0.0.1"):
        raise ValueError("LSS_PUBLIC_BASE_URL must be HTTPS in production")
    return raw


@dataclass(frozen=True)
class StripeConfig:
    secret_key: str
    webhook_secret: str
    public_base_url: str
    base_price_id: str
    pro_price_id: str
    settlement_label: str

    @classmethod
    def from_env(cls) -> "StripeConfig":
        return cls(
            secret_key=(os.getenv("STRIPE_SECRET_KEY") or "").strip(),
            webhook_secret=(os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip(),
            public_base_url=(os.getenv("LSS_PUBLIC_BASE_URL") or "").strip(),
            base_price_id=(os.getenv("STRIPE_BASE_PRICE_ID") or "").strip(),
            pro_price_id=(os.getenv("STRIPE_PRO_PRICE_ID") or "").strip(),
            settlement_label=(os.getenv("LSS_STRIPE_SETTLEMENT_LABEL") or "").strip(),
        )

    @property
    def checkout_configured(self) -> bool:
        return bool(self.secret_key and self.public_base_url and self.base_price_id and self.pro_price_id)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.webhook_secret)

    def price_id(self, plan_id: str) -> str:
        plan = get_plan(plan_id)
        if plan.id == "base":
            value = self.base_price_id
        elif plan.id == "pro":
            value = self.pro_price_id
        else:
            raise ValueError("Stripe subscription checkout accepts Basic or Pro only")
        if not value:
            raise ValueError(f"Stripe price id is not configured for {plan.name}")
        return value


@dataclass(frozen=True)
class CreditPack:
    id: str
    label: str
    stripe_price_id: str
    credits: int
    amount_minor: int
    currency: str = "GBP"


def credit_packs() -> dict[str, CreditPack]:
    raw = (os.getenv("LSS_STRIPE_CREDIT_PACKS_JSON") or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("LSS_STRIPE_CREDIT_PACKS_JSON is invalid JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("Stripe credit packs must be a JSON list")
    result: dict[str, CreditPack] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each Stripe credit pack must be an object")
        pack = CreditPack(
            id=str(item.get("id") or "").strip(),
            label=str(item.get("label") or "Credit top-up").strip()[:120],
            stripe_price_id=str(item.get("stripe_price_id") or "").strip(),
            credits=int(item.get("credits") or 0),
            amount_minor=int(item.get("amount_minor") or 0),
            currency=str(item.get("currency") or "GBP").strip().upper(),
        )
        if not pack.id or not pack.stripe_price_id or pack.credits <= 0 or pack.amount_minor <= 0:
            raise ValueError("Stripe credit pack requires id, stripe_price_id, positive credits and amount_minor")
        if pack.id in result:
            raise ValueError(f"Duplicate Stripe credit pack id: {pack.id}")
        result[pack.id] = pack
    return result


class StripeEvidenceStore:
    """Idempotent Stripe event evidence and customer/subscription binding store."""

    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.getenv("LSS_DB_PATH") or "data/live_sound_studio.sqlite3"
        self.db_path = Path(configured)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS stripe_billing_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    object_id TEXT,
                    payload_sha256 TEXT NOT NULL,
                    processing_status TEXT NOT NULL,
                    error TEXT,
                    received_at TEXT NOT NULL,
                    processed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_stripe_events_received
                    ON stripe_billing_events(received_at DESC);

                CREATE TABLE IF NOT EXISTS stripe_customer_bindings (
                    user_id TEXT PRIMARY KEY,
                    stripe_customer_id TEXT UNIQUE,
                    stripe_subscription_id TEXT UNIQUE,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def begin_event(self, event: dict[str, Any], raw_body: bytes) -> dict[str, Any]:
        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("type") or "").strip()
        obj = ((event.get("data") or {}).get("object") or {}) if isinstance(event.get("data"), dict) else {}
        object_id = str(obj.get("id") or "").strip() or None
        if not event_id or not event_type:
            raise ValueError("Stripe event is missing id or type")
        digest = hashlib.sha256(raw_body).hexdigest()
        with self._connect() as con:
            existing = con.execute("SELECT * FROM stripe_billing_events WHERE event_id=?", (event_id,)).fetchone()
            if existing:
                row = dict(existing)
                if row["payload_sha256"] != digest or row["event_type"] != event_type:
                    raise ValueError("Stripe event id was reused with different content")
                row["duplicate"] = True
                return row
            con.execute(
                """INSERT INTO stripe_billing_events
                   (event_id,event_type,object_id,payload_sha256,processing_status,error,received_at,processed_at)
                   VALUES (?,?,?,?, 'received',NULL,?,NULL)""",
                (event_id, event_type, object_id, digest, _iso()),
            )
        return {"event_id": event_id, "event_type": event_type, "processing_status": "received", "duplicate": False}

    def finish_event(self, event_id: str, status: str, error: str | None = None) -> None:
        with self._connect() as con:
            con.execute(
                """UPDATE stripe_billing_events SET processing_status=?,error=?,processed_at=? WHERE event_id=?""",
                (status, (error or "")[:500] or None, _iso(), event_id),
            )

    def bind_subscription(self, user_id: str, customer_id: str, subscription_id: str, plan_id: str, status: str = "active") -> None:
        if not customer_id or not subscription_id:
            raise ValueError("Stripe customer and subscription ids are required")
        with self._connect() as con:
            con.execute(
                """INSERT INTO stripe_customer_bindings
                   (user_id,stripe_customer_id,stripe_subscription_id,plan_id,status,updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     stripe_customer_id=excluded.stripe_customer_id,
                     stripe_subscription_id=excluded.stripe_subscription_id,
                     plan_id=excluded.plan_id,status=excluded.status,updated_at=excluded.updated_at""",
                (user_id, customer_id, subscription_id, plan_id, status, _iso()),
            )

    def binding(self, *, subscription_id: str | None = None, customer_id: str | None = None) -> dict | None:
        if not subscription_id and not customer_id:
            return None
        with self._connect() as con:
            if subscription_id:
                row = con.execute("SELECT * FROM stripe_customer_bindings WHERE stripe_subscription_id=?", (subscription_id,)).fetchone()
            else:
                row = con.execute("SELECT * FROM stripe_customer_bindings WHERE stripe_customer_id=?", (customer_id,)).fetchone()
        return dict(row) if row else None

    def set_binding_status(self, user_id: str, status: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE stripe_customer_bindings SET status=?,updated_at=? WHERE user_id=?", (status, _iso(), user_id))


class StripeClient:
    def __init__(self, config: StripeConfig | None = None):
        self.config = config or StripeConfig.from_env()

    def _post(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        if not self.config.secret_key:
            raise RuntimeError("Stripe secret key is not configured")
        try:
            response = httpx.post(
                f"{_STRIPE_API}{path}",
                data=data,
                headers={"Authorization": f"Bearer {self.config.secret_key}"},
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Stripe request failed: {type(exc).__name__}") from exc
        try:
            body = response.json()
        except Exception as exc:
            raise RuntimeError("Stripe returned a non-JSON response") from exc
        if response.status_code >= 400:
            message = ((body.get("error") or {}).get("message") if isinstance(body, dict) else None) or "Stripe rejected the request"
            raise RuntimeError(str(message)[:300])
        if not isinstance(body, dict):
            raise RuntimeError("Stripe returned an invalid response")
        return body

    def subscription_checkout(self, user: dict[str, Any], plan_id: str) -> dict[str, Any]:
        plan = get_plan(plan_id)
        base = _clean_base_url(self.config.public_base_url)
        price_id = self.config.price_id(plan.id)
        return self._post(
            "/v1/checkout/sessions",
            {
                "mode": "subscription",
                "client_reference_id": str(user["id"]),
                "customer_email": str(user.get("email") or ""),
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "success_url": f"{base}/billing/stripe/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{base}/membership/payment?stripe=cancelled",
                "metadata[user_id]": str(user["id"]),
                "metadata[plan_id]": plan.id,
                "metadata[purchase_kind]": "subscription",
                "subscription_data[metadata][user_id]": str(user["id"]),
                "subscription_data[metadata][plan_id]": plan.id,
            },
        )

    def credit_checkout(self, user: dict[str, Any], pack: CreditPack) -> dict[str, Any]:
        base = _clean_base_url(self.config.public_base_url)
        return self._post(
            "/v1/checkout/sessions",
            {
                "mode": "payment",
                "client_reference_id": str(user["id"]),
                "customer_email": str(user.get("email") or ""),
                "line_items[0][price]": pack.stripe_price_id,
                "line_items[0][quantity]": "1",
                "success_url": f"{base}/billing/stripe/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{base}/dashboard?stripe=cancelled",
                "metadata[user_id]": str(user["id"]),
                "metadata[credit_pack_id]": pack.id,
                "metadata[purchase_kind]": "credit_topup",
            },
        )


def verify_webhook_signature(raw_body: bytes, signature_header: str, secret: str, *, now: int | None = None) -> None:
    if not secret:
        raise ValueError("Stripe webhook secret is not configured")
    timestamp: int | None = None
    signatures: list[str] = []
    for component in (signature_header or "").split(","):
        key, _, value = component.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                pass
        elif key == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise ValueError("Stripe-Signature is missing timestamp or v1 signature")
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > _WEBHOOK_TOLERANCE_SECONDS:
        raise ValueError("Stripe webhook timestamp is outside the allowed tolerance")
    signed = str(timestamp).encode("ascii") + b"." + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, value) for value in signatures):
        raise ValueError("Stripe webhook signature verification failed")


def _subscription_id(invoice: dict[str, Any]) -> str:
    direct = invoice.get("subscription")
    if isinstance(direct, str):
        return direct
    parent = invoice.get("parent")
    if isinstance(parent, dict):
        details = parent.get("subscription_details")
        if isinstance(details, dict):
            value = details.get("subscription")
            if isinstance(value, str):
                return value
    return ""


def _billing_reason(invoice: dict[str, Any]) -> str:
    return str(invoice.get("billing_reason") or "").strip()


accounts = AccountStore()
subscriptions = SubscriptionLedger(accounts)
evidence_store = StripeEvidenceStore(accounts.db_path)
credit_store = CreditWalletStore(accounts.db_path)


class SubscriptionCheckoutRequest(BaseModel):
    plan_id: str = Field(pattern="^(base|pro)$")


class CreditCheckoutRequest(BaseModel):
    pack_id: str = Field(min_length=1, max_length=80)


def _session_user(request: Request) -> dict[str, Any]:
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get("lss_session")
    user = accounts.resolve_session(token)
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def _validate_checkout_user(user: dict[str, Any], plan_id: str) -> None:
    status = str(user.get("status") or "")
    if status not in {"approved_pending_payment", "active"}:
        raise HTTPException(403, "Owner approval is required before paid checkout")
    if status == "approved_pending_payment" and user.get("requested_plan_id") != plan_id:
        raise HTTPException(409, "Checkout plan must match the owner-approved requested plan")


@router.get("/status")
def stripe_status():
    config = StripeConfig.from_env()
    packs = credit_packs()
    return {
        "provider": "stripe",
        "checkout_configured": config.checkout_configured,
        "webhook_configured": config.webhook_configured,
        "subscription_plans": ["base", "pro"],
        "credit_topups_configured": bool(packs),
        "bank_details_stored_in_application": False,
        "settlement_destination": "Configured privately in the Stripe Dashboard",
    }


@router.get("/credit-packs")
def stripe_credit_packs():
    packs = credit_packs()
    return {
        "packs": [
            {"id": pack.id, "label": pack.label, "credits": pack.credits, "amount_minor": pack.amount_minor, "currency": pack.currency}
            for pack in packs.values()
        ],
        "bank_details_stored_in_application": False,
    }


@router.post("/checkout/subscription")
def create_subscription_checkout(body: SubscriptionCheckoutRequest, request: Request):
    user = _session_user(request)
    _validate_checkout_user(user, body.plan_id)
    config = StripeConfig.from_env()
    if not config.checkout_configured:
        raise HTTPException(503, "Stripe subscription checkout is not configured")
    try:
        session = StripeClient(config).subscription_checkout(user, body.plan_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    url = str(session.get("url") or "")
    if not url.startswith("https://checkout.stripe.com/"):
        raise HTTPException(502, "Stripe did not return a trusted Checkout URL")
    return {
        "provider": "stripe",
        "checkout_session_id": session.get("id"),
        "checkout_url": url,
        "automatic_activation_from_redirect": False,
        "activation_source": "verified_stripe_webhook",
        "esp_role_effect": "none",
    }


@router.post("/checkout/credits")
def create_credit_checkout(body: CreditCheckoutRequest, request: Request):
    user = _session_user(request)
    if user.get("status") != "active":
        raise HTTPException(403, "Active account required for credit top-ups")
    packs = credit_packs()
    pack = packs.get(body.pack_id)
    if pack is None:
        raise HTTPException(404, "Credit pack not found")
    config = StripeConfig.from_env()
    if not config.secret_key or not config.public_base_url:
        raise HTTPException(503, "Stripe checkout is not configured")
    try:
        session = StripeClient(config).credit_checkout(user, pack)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    url = str(session.get("url") or "")
    if not url.startswith("https://checkout.stripe.com/"):
        raise HTTPException(502, "Stripe did not return a trusted Checkout URL")
    return {
        "provider": "stripe",
        "checkout_session_id": session.get("id"),
        "checkout_url": url,
        "credits": pack.credits,
        "automatic_credit_from_redirect": False,
        "credit_source": "verified_stripe_webhook",
        "subscription_effect": "none",
        "esp_role_effect": "none",
    }


@router.get("/success", response_class=HTMLResponse)
def stripe_success(session_id: str = ""):
    safe = (session_id or "")[:120]
    return HTMLResponse(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Payment received</title></head><body style='font-family:system-ui;background:#08050d;color:#fff;padding:36px'>"
        "<main style='max-width:680px;margin:auto'><h1>Thank you.</h1>"
        "<p>Stripe has returned you to the Command Center. Access or credits are confirmed only after the signed Stripe webhook is verified.</p>"
        f"<p style='opacity:.7'>Checkout session: {safe}</p><p><a href='/dashboard' style='color:#f4c873'>Return to dashboard</a></p></main></body></html>"
    )


@router.post("/webhook")
async def stripe_webhook(request: Request):
    config = StripeConfig.from_env()
    if not config.webhook_configured:
        raise HTTPException(503, "Stripe webhook verification is not configured")
    raw = await request.body()
    try:
        verify_webhook_signature(raw, request.headers.get("stripe-signature", ""), config.webhook_secret)
        event = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not isinstance(event, dict):
        raise HTTPException(400, "Invalid Stripe event")
    try:
        evidence = evidence_store.begin_event(event, raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if evidence.get("duplicate"):
        return {"received": True, "duplicate": True, "event_id": evidence["event_id"], "status": evidence["processing_status"]}

    event_id = str(event["id"])
    event_type = str(event["type"])
    obj = ((event.get("data") or {}).get("object") or {})
    if not isinstance(obj, dict):
        evidence_store.finish_event(event_id, "ignored", "Event object is not a dictionary")
        return {"received": True, "event_id": event_id, "processed": False}

    try:
        result: dict[str, Any] = {"processed": False, "event_type": event_type}
        if event_type == "checkout.session.completed":
            metadata = obj.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            purchase_kind = str(metadata.get("purchase_kind") or "")
            user_id = str(metadata.get("user_id") or obj.get("client_reference_id") or "").strip()
            if not user_id:
                raise ValueError("Stripe Checkout Session has no bound user id")
            user = accounts.get_user(user_id)
            if not user:
                raise ValueError("Stripe Checkout Session references an unknown user")
            if str(obj.get("payment_status") or "") != "paid":
                raise ValueError("Stripe Checkout Session is not paid")

            if purchase_kind == "subscription":
                plan_id = str(metadata.get("plan_id") or "")
                plan = get_plan(plan_id)
                if plan.id == "free":
                    raise ValueError("Free plan cannot be activated from Stripe")
                if user.get("status") == "approved_pending_payment" and user.get("requested_plan_id") != plan.id:
                    raise ValueError("Stripe plan does not match the owner-approved requested plan")
                if int(obj.get("amount_total") or -1) != plan.monthly_price_minor:
                    raise ValueError("Stripe Checkout amount does not match the configured membership price")
                if str(obj.get("currency") or "").upper() != plan.currency:
                    raise ValueError("Stripe Checkout currency does not match the membership currency")
                customer_id = str(obj.get("customer") or "")
                subscription_id = str(obj.get("subscription") or "")
                status = subscriptions.verify_payment(user_id, plan.id, f"stripe:checkout:{obj.get('id')}")
                evidence_store.bind_subscription(user_id, customer_id, subscription_id, plan.id, "active")
                result = {"processed": True, "kind": "subscription", "plan_id": plan.id, "user_id": user_id, "subscription": status["subscription"]}

            elif purchase_kind == "credit_topup":
                if user.get("status") != "active":
                    raise ValueError("Credit top-up user is not active")
                pack_id = str(metadata.get("credit_pack_id") or "")
                pack = credit_packs().get(pack_id)
                if pack is None:
                    raise ValueError("Stripe Checkout references an unknown credit pack")
                if int(obj.get("amount_total") or -1) != pack.amount_minor or str(obj.get("currency") or "").upper() != pack.currency:
                    raise ValueError("Stripe credit top-up amount/currency does not match the configured pack")
                transaction = credit_store.adjust(
                    user_id,
                    pack.credits,
                    kind="purchase",
                    reason=f"Stripe credit top-up — {pack.label}",
                    actor="stripe_webhook",
                    reference=f"stripe:checkout:{obj.get('id')}",
                )
                result = {"processed": True, "kind": "credit_topup", "pack_id": pack.id, "credits": pack.credits, "balance": transaction["balance_after"], "subscription_effect": "none", "esp_role_effect": "none"}
            else:
                result = {"processed": False, "ignored": True, "reason": "unrecognized_checkout_purchase_kind"}

        elif event_type == "invoice.paid":
            subscription_id = _subscription_id(obj)
            customer_id = str(obj.get("customer") or "")
            binding = evidence_store.binding(subscription_id=subscription_id, customer_id=customer_id)
            reason = _billing_reason(obj)
            if reason == "subscription_create":
                result = {"processed": False, "ignored": True, "reason": "initial_invoice_is_covered_by_checkout_completion"}
            elif reason == "subscription_cycle":
                if not binding:
                    raise ValueError("Stripe renewal invoice has no local subscription binding")
                plan = get_plan(binding["plan_id"])
                if str(obj.get("currency") or "").upper() != plan.currency:
                    raise ValueError("Stripe renewal invoice currency does not match the subscription")
                status = subscriptions.verify_payment(binding["user_id"], plan.id, f"stripe:invoice:{obj.get('id')}")
                evidence_store.set_binding_status(binding["user_id"], "active")
                result = {"processed": True, "kind": "subscription_renewal", "user_id": binding["user_id"], "plan_id": plan.id, "subscription": status["subscription"]}
            else:
                result = {"processed": False, "ignored": True, "reason": f"unsupported_invoice_reason:{reason or 'unknown'}"}

        elif event_type == "invoice.payment_failed":
            binding = evidence_store.binding(subscription_id=_subscription_id(obj), customer_id=str(obj.get("customer") or ""))
            if binding:
                evidence_store.set_binding_status(binding["user_id"], "payment_failed")
            result = {"processed": bool(binding), "kind": "payment_failed", "access_removed_immediately": False}

        elif event_type == "customer.subscription.deleted":
            binding = evidence_store.binding(subscription_id=str(obj.get("id") or ""), customer_id=str(obj.get("customer") or ""))
            if binding:
                evidence_store.set_binding_status(binding["user_id"], "cancelled")
            result = {"processed": bool(binding), "kind": "subscription_cancelled", "access_removed_immediately": False}
        else:
            result = {"processed": False, "ignored": True, "reason": "event_type_not_used"}

        evidence_store.finish_event(event_id, "processed" if result.get("processed") else "ignored")
        return {"received": True, "event_id": event_id, **result}
    except Exception as exc:
        evidence_store.finish_event(event_id, "failed", str(exc))
        raise HTTPException(400, str(exc)) from exc


__all__ = [
    "CreditPack",
    "StripeClient",
    "StripeConfig",
    "StripeEvidenceStore",
    "credit_packs",
    "router",
    "verify_webhook_signature",
]

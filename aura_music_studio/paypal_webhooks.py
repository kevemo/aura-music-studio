from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import requests

from .accounts import AccountStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PayPalWebhookError(ValueError):
    pass


class PayPalWebhookVerifier:
    """Verify PayPal REST webhooks by posting the delivery evidence back to PayPal.

    Secrets are read only from environment variables and never persisted or returned.
    The application deliberately does not trust browser redirects or unsigned JSON.
    """

    def __init__(self) -> None:
        self.client_id = os.getenv("LSS_PAYPAL_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("LSS_PAYPAL_CLIENT_SECRET", "").strip()
        self.webhook_id = os.getenv("LSS_PAYPAL_WEBHOOK_ID", "").strip()
        environment = os.getenv("LSS_PAYPAL_ENVIRONMENT", "live").strip().lower()
        self.base_url = (
            "https://api-m.sandbox.paypal.com" if environment == "sandbox" else "https://api-m.paypal.com"
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.webhook_id)

    def _access_token(self) -> str:
        if not self.configured:
            raise PayPalWebhookError("PayPal webhook verification is not configured")
        try:
            response = requests.post(
                f"{self.base_url}/v1/oauth2/token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise PayPalWebhookError("PayPal OAuth verification request failed") from exc
        if response.status_code >= 400:
            raise PayPalWebhookError("PayPal OAuth verification request failed")
        token = (response.json() or {}).get("access_token")
        if not token:
            raise PayPalWebhookError("PayPal did not return an access token")
        return str(token)

    def verify(self, headers: dict[str, str], event: dict) -> bool:
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        required = {
            "transmission_id": normalized.get("paypal-transmission-id", ""),
            "transmission_time": normalized.get("paypal-transmission-time", ""),
            "cert_url": normalized.get("paypal-cert-url", ""),
            "auth_algo": normalized.get("paypal-auth-algo", ""),
            "transmission_sig": normalized.get("paypal-transmission-sig", ""),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise PayPalWebhookError(f"Missing PayPal verification headers: {', '.join(missing)}")

        cert = urlparse(required["cert_url"])
        hostname = (cert.hostname or "").lower()
        if cert.scheme != "https" or not (hostname == "paypal.com" or hostname.endswith(".paypal.com")):
            raise PayPalWebhookError("Untrusted PayPal certificate URL")

        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("event_type") or "").strip()
        if not event_id or not event_type:
            raise PayPalWebhookError("PayPal webhook event id and event_type are required")

        payload = {
            **required,
            "webhook_id": self.webhook_id,
            "webhook_event": event,
        }
        try:
            response = requests.post(
                f"{self.base_url}/v1/notifications/verify-webhook-signature",
                headers={
                    "Authorization": f"Bearer {self._access_token()}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise PayPalWebhookError("PayPal webhook signature verification request failed") from exc
        if response.status_code >= 400:
            raise PayPalWebhookError("PayPal webhook signature verification request failed")
        return str((response.json() or {}).get("verification_status", "")).upper() == "SUCCESS"


class PayPalWebhookEvidenceStore:
    """Idempotent ledger for signature-verified PayPal events."""

    def __init__(self, store: AccountStore | None = None) -> None:
        self.store = store or AccountStore()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.store.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS paypal_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    resource_id TEXT,
                    transmission_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    verified_at TEXT NOT NULL
                )"""
            )

    def record(self, event: dict, transmission_id: str) -> dict:
        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("event_type") or "").strip()
        resource = event.get("resource") if isinstance(event.get("resource"), dict) else {}
        resource_id = str(resource.get("id") or "").strip() or None
        transmission_id = (transmission_id or "").strip()
        if not event_id or not event_type or not transmission_id:
            raise PayPalWebhookError("Verified PayPal event is missing required evidence fields")

        payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
        now = _now_iso()
        with self._connect() as con:
            existing = con.execute(
                """SELECT event_type,resource_id,transmission_id,payload_json
                   FROM paypal_webhook_events WHERE event_id=?""",
                (event_id,),
            ).fetchone()
            if existing:
                stored = dict(existing)
                incoming = {
                    "event_type": event_type,
                    "resource_id": resource_id,
                    "transmission_id": transmission_id,
                    "payload_json": payload,
                }
                if stored != incoming:
                    raise PayPalWebhookError(
                        "PayPal event id conflicts with previously verified delivery evidence"
                    )
                duplicate = True
            else:
                con.execute(
                    """INSERT INTO paypal_webhook_events
                       (event_id,event_type,resource_id,transmission_id,payload_json,verified_at)
                       VALUES (?,?,?,?,?,?)""",
                    (event_id, event_type, resource_id, transmission_id, payload, now),
                )
                duplicate = False
        return {
            "event_id": event_id,
            "event_type": event_type,
            "resource_id": resource_id,
            "duplicate": duplicate,
            "verified": True,
        }

    def get(self, event_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM paypal_webhook_events WHERE event_id=?", ((event_id or "").strip(),)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def recent(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as con:
            rows = con.execute(
                """SELECT event_id,event_type,resource_id,transmission_id,verified_at
                   FROM paypal_webhook_events ORDER BY verified_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def validate_invoice_paid_event(
    event: dict,
    *,
    expected_amount_minor: int,
    expected_currency: str,
    expected_email: str,
) -> str:
    """Return a stable payment reference only for a fully paid, exact-plan invoice.

    PayPal can emit INVOICING.INVOICE.PAID for partial/pending situations, so access is
    granted only when the resource itself reports PAID and the amount/currency/account
    identity all match the intended membership.
    """

    if str(event.get("event_type") or "") != "INVOICING.INVOICE.PAID":
        raise PayPalWebhookError("The verified event is not an invoice-paid event")
    resource = event.get("resource") if isinstance(event.get("resource"), dict) else {}
    if str(resource.get("status") or "").upper() != "PAID":
        raise PayPalWebhookError("The PayPal invoice is not fully paid")

    amount = resource.get("amount") if isinstance(resource.get("amount"), dict) else {}
    currency = str(amount.get("currency_code") or "").upper()
    if currency != expected_currency.upper():
        raise PayPalWebhookError("PayPal invoice currency does not match the membership plan")
    try:
        amount_minor = int((Decimal(str(amount.get("value"))) * 100).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError):
        raise PayPalWebhookError("PayPal invoice amount is invalid") from None
    if amount_minor != int(expected_amount_minor):
        raise PayPalWebhookError("PayPal invoice amount does not match the membership plan")

    payer_email = str(resource.get("payer_email") or "").strip().lower()
    if not payer_email:
        recipients = resource.get("primary_recipients")
        if isinstance(recipients, list) and recipients and isinstance(recipients[0], dict):
            billing_info = recipients[0].get("billing_info")
            if isinstance(billing_info, dict):
                payer_email = str(billing_info.get("email_address") or "").strip().lower()
    if not payer_email or payer_email != (expected_email or "").strip().lower():
        raise PayPalWebhookError("PayPal payer identity does not match the approved account")

    event_id = str(event.get("id") or "").strip()
    if not event_id:
        raise PayPalWebhookError("PayPal event id is missing")
    return f"paypal:{event_id}"

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI

from aura_music_studio.access_control import PUBLIC_PREFIXES
from aura_music_studio.creative_version_autopromotion import router as overlay_router
from aura_music_studio.stripe_billing import credit_packs, verify_webhook_signature
from aura_music_studio.stripe_billing_hardening import (
    hardened_stripe_success,
    validate_subscription_cycle_invoice,
)


def _signature(secret: str, raw: bytes, timestamp: int) -> str:
    signed = str(timestamp).encode("ascii") + b"." + raw
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_stripe_routes_are_mounted_through_existing_overlay():
    # FastAPI 0.137+ preserves included routers in a live route tree instead of flattening
    # every child path directly into APIRouter.routes. Build the effective application route
    # set through OpenAPI so this regression test verifies real routability rather than a
    # private implementation detail of FastAPI's router tree.
    effective_app = FastAPI()
    effective_app.include_router(overlay_router)
    paths = set(effective_app.openapi()["paths"])
    assert "/billing/stripe/status" in paths
    assert "/billing/stripe/checkout/subscription" in paths
    assert "/billing/stripe/checkout/credits" in paths
    assert "/billing/stripe/webhook" in paths


def test_stripe_prefix_bypasses_only_active_member_gate_for_self_verification():
    assert "/billing/stripe/" in PUBLIC_PREFIXES
    assert "/billing/" not in PUBLIC_PREFIXES


def test_webhook_signature_accepts_current_valid_signature():
    secret = "whsec_test_only"
    raw = b'{"id":"evt_test","type":"checkout.session.completed"}'
    verify_webhook_signature(raw, _signature(secret, raw, 1_700_000_000), secret, now=1_700_000_120)


def test_webhook_signature_rejects_modified_payload():
    secret = "whsec_test_only"
    original = b'{"id":"evt_test","type":"checkout.session.completed"}'
    modified = b'{"id":"evt_test","type":"invoice.paid"}'
    with pytest.raises(ValueError, match="signature verification failed"):
        verify_webhook_signature(modified, _signature(secret, original, 1_700_000_000), secret, now=1_700_000_100)


def test_webhook_signature_rejects_replay_outside_tolerance():
    secret = "whsec_test_only"
    raw = b'{"id":"evt_test"}'
    with pytest.raises(ValueError, match="outside the allowed tolerance"):
        verify_webhook_signature(raw, _signature(secret, raw, 1_700_000_000), secret, now=1_700_000_301)


def test_credit_packs_are_configuration_only_and_do_not_contain_bank_details(monkeypatch):
    monkeypatch.setenv(
        "LSS_STRIPE_CREDIT_PACKS_JSON",
        json.dumps(
            [
                {
                    "id": "credits-100",
                    "label": "100 credits",
                    "stripe_price_id": "price_test_100",
                    "credits": 100,
                    "amount_minor": 299,
                    "currency": "GBP",
                }
            ]
        ),
    )
    packs = credit_packs()
    pack = packs["credits-100"]
    assert pack.credits == 100
    assert pack.amount_minor == 299
    assert pack.currency == "GBP"
    assert not hasattr(pack, "account_number")
    assert not hasattr(pack, "sort_code")


def test_stripe_return_page_escapes_untrusted_session_id():
    response = hardened_stripe_success("cs_test_<script>alert('x')</script>")
    body = response.body.decode("utf-8")
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def _base_binding() -> dict:
    return {
        "user_id": "user_test",
        "plan_id": "base",
        "stripe_customer_id": "cus_bound",
        "stripe_subscription_id": "sub_bound",
    }


def _paid_base_invoice() -> dict:
    return {
        "id": "in_test",
        "status": "paid",
        "billing_reason": "subscription_cycle",
        "currency": "gbp",
        "amount_paid": 499,
        "customer": "cus_bound",
        "subscription": "sub_bound",
    }


def test_subscription_cycle_requires_exact_bound_paid_invoice():
    validate_subscription_cycle_invoice(_paid_base_invoice(), _base_binding())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("amount_paid", 498, "paid amount"),
        ("currency", "usd", "currency"),
        ("customer", "cus_other", "customer"),
        ("subscription", "sub_other", "subscription"),
        ("status", "open", "not paid"),
    ],
)
def test_subscription_cycle_rejects_billing_drift(field, value, message):
    invoice = _paid_base_invoice()
    invoice[field] = value
    with pytest.raises(ValueError, match=message):
        validate_subscription_cycle_invoice(invoice, _base_binding())

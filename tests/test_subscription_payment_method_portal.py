from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.audit import AuditLedger
from aura_music_studio.csrf_tokens import CSRF_HEADER, SessionCsrfService
from aura_music_studio.membership_billing_periods import MembershipBillingPreferenceStore
from aura_music_studio.stripe_billing import StripeEvidenceStore
from aura_music_studio.subscriptions import SubscriptionLedger
import aura_music_studio.subscription_lifecycle_api as lifecycle_api


def _paid_user(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("member@example.com", "Paid Member", "verysecurepassword", "base")
    prefs = MembershipBillingPreferenceStore(store)
    prefs.record_request(
        user_id=signup.user_id,
        membership_request_id=signup.membership_request_id,
        plan_id="base",
        billing_period="monthly",
    )
    store.decide_membership(signup.approval_token, "approve", "Kev")
    prefs.decide(signup.membership_request_id, approved=True)
    ledger = SubscriptionLedger(store)
    ledger.verify_payment(signup.user_id, "base", "PAYMENT-REFERENCE-1234")
    token = store.create_session(signup.user_id)
    return store, ledger, signup.user_id, token


def _client(monkeypatch, store, ledger, stripe_store=None):
    monkeypatch.setattr(lifecycle_api, "store", store)
    monkeypatch.setattr(lifecycle_api, "subscriptions", ledger)
    monkeypatch.setattr(lifecycle_api, "audit", AuditLedger(store))
    monkeypatch.setattr(lifecycle_api, "evidence_store", stripe_store or StripeEvidenceStore(store.db_path))
    app = FastAPI()
    app.include_router(lifecycle_api.router)
    return TestClient(app)


def test_payment_method_management_requires_sign_in(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    client = _client(monkeypatch, store, SubscriptionLedger(store))
    assert client.post("/membership/subscription/payment-method").status_code == 401


def test_payment_method_management_requires_stripe_binding(tmp_path, monkeypatch):
    store, ledger, _user_id, token = _paid_user(tmp_path)
    client = _client(monkeypatch, store, ledger)
    response = client.post(
        "/membership/subscription/payment-method",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "This membership is not managed by Stripe"


def test_cookie_payment_method_management_requires_session_bound_csrf(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "active")
    client = _client(monkeypatch, store, ledger, stripe_store)
    client.cookies.set("lss_session", token)

    denied = client.post("/membership/subscription/payment-method")
    assert denied.status_code == 403
    assert denied.json()["detail"]["security_gate"] == "session_csrf"

    csrf = SessionCsrfService(store).issue(token)["token"]
    monkeypatch.setenv("STRIPE_SECRET_KEY", "configured-test-secret")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")

    def fake_post(self, path, data):
        return {
            "id": "bps_test_123",
            "customer": "cus_member_123",
            "url": "https://billing.stripe.com/p/session/test_123",
        }

    monkeypatch.setattr(lifecycle_api.StripeClient, "_post", fake_post)
    allowed = client.post(
        "/membership/subscription/payment-method",
        headers={CSRF_HEADER: csrf},
    )
    assert allowed.status_code == 200


def test_stripe_payment_method_management_uses_bound_customer_and_constrained_flow(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "active")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "configured-test-secret")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")
    calls = []

    def fake_post(self, path, data):
        calls.append((path, dict(data)))
        return {
            "id": "bps_test_123",
            "customer": "cus_member_123",
            "url": "https://billing.stripe.com/p/session/test_123",
        }

    monkeypatch.setattr(lifecycle_api.StripeClient, "_post", fake_post)
    response = client.post(
        "/membership/subscription/payment-method",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert calls == [
        (
            "/v1/billing_portal/sessions",
            {
                "customer": "cus_member_123",
                "return_url": "https://command.example/dashboard",
                "flow_data[type]": "payment_method_update",
                "flow_data[after_completion][type]": "redirect",
                "flow_data[after_completion][redirect][return_url]": "https://command.example/dashboard",
            },
        )
    ]
    payload = response.json()
    assert payload["provider"] == "stripe"
    assert payload["flow"] == "payment_method_update"
    assert payload["payment_method_management_only"] is True
    assert payload["plan_or_billing_period_change_enabled_by_this_endpoint"] is False
    assert payload["entitlement_changed"] is False
    assert payload["browser_return_is_payment_proof"] is False
    assert payload["portal_url"].startswith("https://billing.stripe.com/")
    assert "cus_member_123" not in str(payload)
    assert "sub_member_123" not in str(payload)


def test_stripe_payment_method_management_missing_configuration_fails_closed(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "active")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")

    response = client.post(
        "/membership/subscription/payment-method",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 503


def test_stripe_payment_method_management_rejects_provider_customer_mismatch(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "active")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "configured-test-secret")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")

    def fake_post(self, path, data):
        return {"customer": "cus_wrong", "url": "https://billing.stripe.com/p/session/test_123"}

    monkeypatch.setattr(lifecycle_api.StripeClient, "_post", fake_post)
    response = client.post(
        "/membership/subscription/payment-method",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 502


def test_stripe_payment_method_management_rejects_untrusted_portal_url(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "active")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "configured-test-secret")
    monkeypatch.setenv("LSS_PUBLIC_BASE_URL", "https://command.example")

    def fake_post(self, path, data):
        return {"customer": "cus_member_123", "url": "https://untrusted.example/portal"}

    monkeypatch.setattr(lifecycle_api.StripeClient, "_post", fake_post)
    response = client.post(
        "/membership/subscription/payment-method",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 502

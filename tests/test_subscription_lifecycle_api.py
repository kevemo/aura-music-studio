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


def test_subscription_status_requires_sign_in(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    client = _client(monkeypatch, store, SubscriptionLedger(store))
    assert client.get("/membership/subscription").status_code == 401


def test_subscription_status_hides_payment_references(tmp_path, monkeypatch):
    store, ledger, _user_id, token = _paid_user(tmp_path)
    client = _client(monkeypatch, store, ledger)
    response = client.get("/membership/subscription", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["plan_id"] == "base"
    assert payload["subscription"]["billing_period"] == "monthly"
    assert payload["browser_redirect_is_payment_or_refund_proof"] is False
    assert payload["esp_role_effect"] == "none"
    assert "payment_reference" not in str(payload)
    assert "PAYMENT-REFERENCE-1234" not in str(payload)


def test_manual_member_cancel_preserves_current_paid_access(tmp_path, monkeypatch):
    store, ledger, _user_id, token = _paid_user(tmp_path)
    client = _client(monkeypatch, store, ledger)
    response = client.post("/membership/subscription/cancel", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["cancellation_requested"] is True
    assert payload["access_removed_immediately"] is False
    assert payload["plan_id"] == "base"
    assert payload["cancel_at_period_end"] is True
    assert payload["subscription"]["status"] == "cancel_at_period_end"
    assert payload["renewal_provider"] == "manual_or_non_stripe"
    assert payload["provider_cancellation_confirmed"] is False


def test_manual_member_can_resume_cancelled_renewal_without_changing_paid_term(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    client = _client(monkeypatch, store, ledger)
    before = ledger.get(user_id)

    cancelled = client.post("/membership/subscription/cancel", headers={"Authorization": f"Bearer {token}"})
    assert cancelled.status_code == 200
    resumed = client.post("/membership/subscription/resume", headers={"Authorization": f"Bearer {token}"})

    assert resumed.status_code == 200
    payload = resumed.json()
    assert payload["renewal_resumed"] is True
    assert payload["cancel_at_period_end"] is False
    assert payload["subscription"]["status"] == "active"
    assert payload["renewal_provider"] == "manual_or_non_stripe"
    assert payload["provider_resume_confirmed"] is False
    assert payload["access_changed_immediately"] is False
    after = ledger.get(user_id)
    assert after["plan_id"] == before["plan_id"]
    assert after["billing_period"] == before["billing_period"]
    assert after["period_start"] == before["period_start"]
    assert after["period_end"] == before["period_end"]


def test_resume_clears_cancellation_on_paid_future_transition(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    ledger.verify_payment(user_id, "pro", "FUTURE-PAYMENT-9999", billing_period="annual")
    client = _client(monkeypatch, store, ledger)

    cancelled = client.post("/membership/subscription/cancel", headers={"Authorization": f"Bearer {token}"})
    assert cancelled.status_code == 200
    assert cancelled.json()["scheduled_transition"]["cancel_at_period_end"] is True
    assert ledger.get(user_id)["status"] == "active"

    resumed = client.post("/membership/subscription/resume", headers={"Authorization": f"Bearer {token}"})
    assert resumed.status_code == 200
    payload = resumed.json()
    assert payload["cancel_at_period_end"] is False
    assert payload["scheduled_transition"]["cancel_at_period_end"] is False
    assert payload["scheduled_transition"]["target_plan_id"] == "pro"
    assert payload["scheduled_transition"]["target_billing_period"] == "annual"
    assert ledger.get(user_id)["status"] == "active"


def test_resume_rejects_membership_that_is_not_scheduled_for_cancellation(tmp_path, monkeypatch):
    store, ledger, _user_id, token = _paid_user(tmp_path)
    client = _client(monkeypatch, store, ledger)
    response = client.post("/membership/subscription/resume", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409
    assert "not currently scheduled for cancellation" in response.json()["detail"]


def test_cookie_member_cancel_requires_session_bound_csrf(tmp_path, monkeypatch):
    store, ledger, _user_id, token = _paid_user(tmp_path)
    client = _client(monkeypatch, store, ledger)
    client.cookies.set("lss_session", token)
    denied = client.post("/membership/subscription/cancel")
    assert denied.status_code == 403
    assert denied.json()["detail"]["security_gate"] == "session_csrf"
    csrf = SessionCsrfService(store).issue(token)["token"]
    response = client.post("/membership/subscription/cancel", headers={CSRF_HEADER: csrf})
    assert response.status_code == 200
    assert response.json()["cancel_at_period_end"] is True


def test_cookie_member_resume_requires_session_bound_csrf(tmp_path, monkeypatch):
    store, ledger, _user_id, token = _paid_user(tmp_path)
    client = _client(monkeypatch, store, ledger)
    assert client.post("/membership/subscription/cancel", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    client.cookies.set("lss_session", token)

    denied = client.post("/membership/subscription/resume")
    assert denied.status_code == 403
    assert denied.json()["detail"]["security_gate"] == "session_csrf"

    csrf = SessionCsrfService(store).issue(token)["token"]
    response = client.post("/membership/subscription/resume", headers={CSRF_HEADER: csrf})
    assert response.status_code == 200
    assert response.json()["cancel_at_period_end"] is False


def test_stripe_member_cancel_updates_bound_provider_before_local_state(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "active")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_subscription_cancel")
    calls = []

    def fake_post(self, path, data):
        calls.append((path, dict(data)))
        return {"id": "sub_member_123", "customer": "cus_member_123", "cancel_at_period_end": True, "status": "active"}

    monkeypatch.setattr(lifecycle_api.StripeClient, "_post", fake_post)
    response = client.post("/membership/subscription/cancel", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert calls == [("/v1/subscriptions/sub_member_123", {"cancel_at_period_end": "true"})]
    assert ledger.get(user_id)["status"] == "cancel_at_period_end"
    assert stripe_store.binding(subscription_id="sub_member_123")["status"] == "cancel_at_period_end"


def test_stripe_member_resume_updates_bound_provider_before_local_state(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "active")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_subscription_resume")
    calls = []

    def fake_post(self, path, data):
        calls.append((path, dict(data)))
        enabled = data["cancel_at_period_end"] == "true"
        return {
            "id": "sub_member_123",
            "customer": "cus_member_123",
            "cancel_at_period_end": enabled,
            "status": "active",
        }

    monkeypatch.setattr(lifecycle_api.StripeClient, "_post", fake_post)
    cancelled = client.post("/membership/subscription/cancel", headers={"Authorization": f"Bearer {token}"})
    assert cancelled.status_code == 200
    resumed = client.post("/membership/subscription/resume", headers={"Authorization": f"Bearer {token}"})

    assert resumed.status_code == 200
    assert calls == [
        ("/v1/subscriptions/sub_member_123", {"cancel_at_period_end": "true"}),
        ("/v1/subscriptions/sub_member_123", {"cancel_at_period_end": "false"}),
    ]
    payload = resumed.json()
    assert payload["renewal_provider"] == "stripe"
    assert payload["provider_resume_confirmed"] is True
    assert payload["cancel_at_period_end"] is False
    assert ledger.get(user_id)["status"] == "active"
    assert store.get_user(user_id)["billing_status"] == "active"
    assert stripe_store.binding(subscription_id="sub_member_123")["status"] == "active"


def test_stripe_cancel_configuration_failure_does_not_change_local_state(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "active")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    response = client.post("/membership/subscription/cancel", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503
    assert ledger.get(user_id)["status"] == "active"
    assert store.get_user(user_id)["billing_status"] == "active"
    assert stripe_store.binding(subscription_id="sub_member_123")["status"] == "active"


def test_stripe_resume_configuration_failure_does_not_change_local_state(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    ledger.cancel_at_period_end(user_id)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "cancel_at_period_end")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    response = client.post("/membership/subscription/resume", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert ledger.get(user_id)["status"] == "cancel_at_period_end"
    assert store.get_user(user_id)["billing_status"] == "cancel_at_period_end"
    assert stripe_store.binding(subscription_id="sub_member_123")["status"] == "cancel_at_period_end"


def test_stripe_cancel_identity_mismatch_fails_closed_without_local_change(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "active")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_subscription_cancel")

    def fake_post(self, path, data):
        return {"id": "sub_wrong", "customer": "cus_member_123", "cancel_at_period_end": True}

    monkeypatch.setattr(lifecycle_api.StripeClient, "_post", fake_post)
    response = client.post("/membership/subscription/cancel", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 502
    assert ledger.get(user_id)["status"] == "active"
    assert store.get_user(user_id)["billing_status"] == "active"


def test_stripe_resume_identity_mismatch_fails_closed_without_local_change(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    ledger.cancel_at_period_end(user_id)
    stripe_store = StripeEvidenceStore(store.db_path)
    stripe_store.bind_subscription(user_id, "cus_member_123", "sub_member_123", "base", "cancel_at_period_end")
    client = _client(monkeypatch, store, ledger, stripe_store)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_subscription_resume")

    def fake_post(self, path, data):
        return {"id": "sub_wrong", "customer": "cus_member_123", "cancel_at_period_end": False}

    monkeypatch.setattr(lifecycle_api.StripeClient, "_post", fake_post)
    response = client.post("/membership/subscription/resume", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 502
    assert ledger.get(user_id)["status"] == "cancel_at_period_end"
    assert store.get_user(user_id)["billing_status"] == "cancel_at_period_end"
    assert stripe_store.binding(subscription_id="sub_member_123")["status"] == "cancel_at_period_end"


def test_verified_refund_requires_admin_and_never_returns_provider_references(tmp_path, monkeypatch):
    store, ledger, user_id, _token = _paid_user(tmp_path)
    client = _client(monkeypatch, store, ledger)
    monkeypatch.setenv("LSS_ADMIN_KEY", "owner-secret")
    body = {"user_id": user_id, "payment_reference": "PAYMENT-REFERENCE-1234", "refund_reference": "REFUND-REFERENCE-5678"}
    denied = client.post("/admin/membership/record-verified-refund", json=body)
    assert denied.status_code == 403
    response = client.post("/admin/membership/record-verified-refund", json=body, headers={"x-lss-admin-key": "owner-secret"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["recorded"] is True
    assert payload["refund_outcome"] == "current_term_refunded_entitlement_revoked"
    assert payload["plan_id"] == "free"
    assert "PAYMENT-REFERENCE-1234" not in str(payload)
    assert "REFUND-REFERENCE-5678" not in str(payload)


def test_future_transition_status_is_sanitized(tmp_path, monkeypatch):
    store, ledger, user_id, token = _paid_user(tmp_path)
    ledger.verify_payment(user_id, "pro", "FUTURE-PAYMENT-9999", billing_period="annual")
    client = _client(monkeypatch, store, ledger)
    response = client.get("/membership/subscription", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["scheduled_transition"]["target_plan_id"] == "pro"
    assert payload["scheduled_transition"]["target_billing_period"] == "annual"
    assert "FUTURE-PAYMENT-9999" not in str(payload)
    assert "payment_reference" not in str(payload)

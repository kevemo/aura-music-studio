from __future__ import annotations

import json

import pytest
from fastapi import HTTPException, Request

from aura_music_studio.accounts import AccountStore
from aura_music_studio.billing_history import BillingHistoryService
from aura_music_studio.membership import MembershipService
from aura_music_studio.membership_billing_periods import MembershipBillingPreferenceStore
from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.subscriptions import SubscriptionLedger
import aura_music_studio.web_portal as web_portal


def _request(path: str, *, cookie: str | None = None, bearer: str | None = None, query: str = "") -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    if bearer:
        headers.append((b"authorization", f"Bearer {bearer}".encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query.encode("ascii"),
            "headers": headers,
            "client": ("127.0.0.1", 10000),
            "server": ("testserver", 80),
        }
    )


def _paid_user(
    store: AccountStore,
    preferences: MembershipBillingPreferenceStore,
    *,
    email: str,
    name: str,
    plan_id: str,
    billing_period: BillingPeriod,
    payment_reference: str,
):
    signup = store.signup(email, name, "verysecurepassword", plan_id)
    preferences.record_request(
        user_id=signup.user_id,
        membership_request_id=signup.membership_request_id,
        plan_id=plan_id,
        billing_period=billing_period,
    )
    store.decide_membership(signup.approval_token, "approve", "Kev")
    preferences.decide(signup.membership_request_id, approved=True)
    ledger = SubscriptionLedger(store)
    ledger.verify_payment(
        signup.user_id,
        plan_id,
        payment_reference,
        billing_period=billing_period,
    )
    return signup, ledger


def _install_web(monkeypatch, store: AccountStore):
    monkeypatch.setattr(web_portal, "store", store)
    monkeypatch.setattr(web_portal, "memberships", MembershipService(store))
    monkeypatch.setattr(web_portal, "billing_preferences", MembershipBillingPreferenceStore(store))


def test_customer_history_is_account_scoped_canonical_and_secret_free(tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    preferences = MembershipBillingPreferenceStore(store)
    first, first_ledger = _paid_user(
        store,
        preferences,
        email="first@example.com",
        name="First Account",
        plan_id="base",
        billing_period=BillingPeriod.MONTHLY,
        payment_reference="PAY-FIRST-001",
    )
    second, _ = _paid_user(
        store,
        preferences,
        email="second@example.com",
        name="Second Account",
        plan_id="pro",
        billing_period=BillingPeriod.ANNUAL,
        payment_reference="PAY-SECOND-999",
    )
    first_ledger.record_verified_refund(first.user_id, "PAY-FIRST-001", "REF-FIRST-001")

    history = BillingHistoryService(store).for_user(first.user_id)
    serialized = json.dumps(history)

    assert history["customer_scoped"] is True
    assert history["read_only"] is True
    assert history["payment_count"] == 1
    assert history["refund_count"] == 1
    payment = history["payments"][0]
    assert payment["plan_id"] == "base"
    assert payment["plan_name"] == "Basic"
    assert payment["billing_period"] == "monthly"
    assert payment["amount"] == "4.99"
    assert payment["amount_minor"] == 499
    assert payment["currency"] == "GBP"
    assert payment["display_amount"] == "£4.99"
    assert payment["canonical_catalogue_match"] is True
    assert payment["provider_invoice_url"] is None
    assert payment["independent_provider_settlement_evidence_presented"] is False
    assert history["refunds"][0]["refund_reference"] == "REF-FIRST-001"
    assert history["refunds"][0]["amount"] is None
    assert history["refunds"][0]["amount_known"] is False
    assert "PAY-SECOND-999" not in serialized
    assert second.user_id not in serialized
    assert "password_hash" not in serialized
    assert "password_salt" not in serialized
    assert "token_hash" not in serialized
    assert "payload_hash" not in serialized


def test_json_history_uses_signed_in_identity_and_ignores_query_account_injection(monkeypatch, tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    preferences = MembershipBillingPreferenceStore(store)
    first, _ = _paid_user(
        store,
        preferences,
        email="signedin@example.com",
        name="Signed In",
        plan_id="base",
        billing_period=BillingPeriod.MONTHLY,
        payment_reference="PAY-SIGNED-IN",
    )
    second, _ = _paid_user(
        store,
        preferences,
        email="other@example.com",
        name="Other User",
        plan_id="pro",
        billing_period=BillingPeriod.MONTHLY,
        payment_reference="PAY-OTHER-ACCOUNT",
    )
    token = store.create_session(first.user_id)
    _install_web(monkeypatch, store)

    result = web_portal.billing_history_json(
        _request(
            "/auth/me/billing-history",
            cookie=f"lss_session={token}",
            query=f"account_id={second.user_id}",
        )
    )
    serialized = json.dumps(result)
    assert "PAY-SIGNED-IN" in serialized
    assert "PAY-OTHER-ACCOUNT" not in serialized
    assert second.user_id not in serialized


def test_json_history_accepts_server_session_bearer_but_rejects_missing_session(monkeypatch, tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    signup = store.signup("pending@example.com", "Pending Account", "verysecurepassword", "free")
    token = store.create_session(signup.user_id)
    _install_web(monkeypatch, store)

    result = web_portal.billing_history_json(_request("/auth/me/billing-history", bearer=token))
    assert result["account"]["status"] == "pending_approval"
    assert result["payments"] == []

    with pytest.raises(HTTPException) as exc_info:
        web_portal.billing_history_json(_request("/auth/me/billing-history"))
    assert exc_info.value.status_code == 401


def test_billing_history_page_is_human_readable_and_dashboard_links_to_it(monkeypatch, tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    preferences = MembershipBillingPreferenceStore(store)
    signup, _ = _paid_user(
        store,
        preferences,
        email="history-page@example.com",
        name="History Page",
        plan_id="pro",
        billing_period=BillingPeriod.ANNUAL,
        payment_reference="PAY-HISTORY-PAGE",
    )
    token = store.create_session(signup.user_id)
    _install_web(monkeypatch, store)
    request = _request("/auth/billing-history", cookie=f"lss_session={token}")

    page = web_portal.billing_history_page(request)
    body = page.body.decode("utf-8")
    assert page.status_code == 200
    assert "Billing history" in body
    assert "£99.00" in body
    assert "Unlimited Pro" in body
    assert "PAY-HISTORY-PAGE" in body
    assert "read-only view" in body
    assert "does not independently re-verify bank or provider settlement" in body
    assert "href='None'" not in body

    dashboard = web_portal.dashboard(_request("/dashboard", cookie=f"lss_session={token}"))
    assert "href='/auth/billing-history'" in dashboard.body.decode("utf-8")


def test_billing_history_routes_are_mounted_in_live_fastapi_app():
    from aura_music_studio.api import app

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/auth/me/billing-history" in paths
    assert "/auth/billing-history" in paths

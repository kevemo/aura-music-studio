from __future__ import annotations

from fastapi import Request

from aura_music_studio.accounts import AccountStore
from aura_music_studio.membership import MembershipService
from aura_music_studio.membership_billing_periods import MembershipBillingPreferenceStore
from aura_music_studio.native_products import BillingPeriod
import aura_music_studio.web_portal as web_portal


def _request(path: str = "/", *, cookie: str | None = None) -> Request:
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 10000),
            "server": ("testserver", 80),
        }
    )


def _install(monkeypatch, tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    preferences = MembershipBillingPreferenceStore(store)
    monkeypatch.setattr(web_portal, "store", store)
    monkeypatch.setattr(web_portal, "memberships", MembershipService(store))
    monkeypatch.setattr(web_portal, "billing_preferences", preferences)
    monkeypatch.setattr(web_portal, "notify_membership_request", lambda **kwargs: {"delivered": False})
    return store, preferences


def test_pricing_cards_offer_yearly_only_for_unlimited_pro():
    html = web_portal._pricing_cards()
    assert "£4.99/month" in html
    assert "£9.99/month" in html
    assert "£99.00/year" in html
    assert "/signup?plan=pro&amp;billing_period=annual" not in html  # raw internal HTML is not entity-escaped
    assert "/signup?plan=pro&billing_period=annual" in html
    assert "/signup?plan=base&billing_period=annual" not in html


def test_public_signup_records_owner_reviewable_annual_pro_period(monkeypatch, tmp_path):
    store, preferences = _install(monkeypatch, tmp_path)
    response = web_portal.signup_submit(
        _request("/signup"),
        display_name="Annual Pro",
        email="annual-web@example.com",
        password="verysecurepassword",
        plan_id="pro",
        billing_period="annual",
    )
    assert response.status_code == 200
    user = store.get_user_by_email("annual-web@example.com")
    assert user is not None
    preference = preferences.for_user(user["id"])
    assert preference is not None
    assert preference["plan_id"] == "pro"
    assert preference["billing_period"] == BillingPeriod.ANNUAL.value
    assert preference["status"] == "requested"
    assert b"yearly" in response.body


def test_basic_annual_signup_fails_closed_before_account_creation(monkeypatch, tmp_path):
    store, _ = _install(monkeypatch, tmp_path)
    response = web_portal.signup_submit(
        _request("/signup"),
        display_name="Invalid Basic Annual",
        email="basic-annual-web@example.com",
        password="verysecurepassword",
        plan_id="base",
        billing_period="annual",
    )
    assert response.status_code == 200
    assert store.get_user_by_email("basic-annual-web@example.com") is None
    assert b"Annual billing is not available for plan: base" in response.body


def test_annual_pro_dashboard_never_reuses_monthly_paypal_route(monkeypatch, tmp_path):
    store, preferences = _install(monkeypatch, tmp_path)
    monkeypatch.delenv("LSS_PAYPAL_PRO_ANNUAL_URL", raising=False)

    signup = store.signup("annual-dashboard@example.com", "Annual Dashboard", "verysecurepassword", "pro")
    preferences.record_request(
        user_id=signup.user_id,
        membership_request_id=signup.membership_request_id,
        plan_id="pro",
        billing_period=BillingPeriod.ANNUAL,
    )
    store.decide_membership(signup.approval_token, "approve", "Kev")
    preferences.decide(signup.membership_request_id, approved=True)
    token = store.create_session(signup.user_id)

    response = web_portal.dashboard(_request("/dashboard", cookie=f"lss_session={token}"))
    body = response.body.decode("utf-8")
    assert "£99.00/year" in body
    assert "must be configured before activation" in body
    assert "678LURGCLH77JDGH" not in body
    assert "href=''" not in body

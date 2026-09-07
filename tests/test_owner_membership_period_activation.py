from __future__ import annotations

from fastapi import Request

from aura_music_studio.accounts import AccountStore
from aura_music_studio.membership_billing_periods import MembershipBillingPreferenceStore
from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.subscriptions import SubscriptionLedger
import aura_music_studio.admin_portal as admin_portal


class _PublicAddressStub:
    def read_status(self):
        return {}


def _request(path: str = "/owner/dashboard") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 10000),
            "server": ("testserver", 80),
        }
    )


def _install(monkeypatch, tmp_path):
    store = AccountStore(tmp_path / "accounts.sqlite3")
    preferences = MembershipBillingPreferenceStore(store)
    monkeypatch.setattr(admin_portal, "store", store)
    monkeypatch.setattr(admin_portal, "subscriptions", SubscriptionLedger(store))
    monkeypatch.setattr(admin_portal, "billing_preferences", preferences)
    monkeypatch.setattr(admin_portal, "public_address", _PublicAddressStub())
    monkeypatch.setattr(admin_portal, "_authorized", lambda request: True)
    return store, preferences


def _annual_approved(store, preferences):
    signup = store.signup("owner-annual@example.com", "Owner Annual", "verysecurepassword", "pro")
    preferences.record_request(
        user_id=signup.user_id,
        membership_request_id=signup.membership_request_id,
        plan_id="pro",
        billing_period=BillingPeriod.ANNUAL,
    )
    store.decide_membership(signup.approval_token, "approve", "Kev")
    preferences.decide(signup.membership_request_id, approved=True)
    return signup


def test_owner_queue_displays_approved_annual_pro_amount(monkeypatch, tmp_path):
    store, preferences = _install(monkeypatch, tmp_path)
    signup = _annual_approved(store, preferences)

    queue = admin_portal._payment_queue()
    assert len(queue) == 1
    assert queue[0]["id"] == signup.user_id
    assert queue[0]["billing_period"] == "annual"
    assert queue[0]["display_amount"] == "£99.00/year"

    page = admin_portal.owner_dashboard(_request())
    body = page.body.decode("utf-8")
    assert "PRO · ANNUAL" in body
    assert "£99.00/year" in body
    assert "activate approved annual term" in body
    assert "31 days" not in body


def test_owner_activation_uses_server_approved_annual_period(monkeypatch, tmp_path):
    store, preferences = _install(monkeypatch, tmp_path)
    signup = _annual_approved(store, preferences)

    response = admin_portal.owner_activate_payment(
        _request("/owner/activate-payment"),
        user_id=signup.user_id,
        plan_id="pro",
        payment_reference="OWNER-VERIFIED-ANNUAL-1",
    )
    state = admin_portal.subscriptions.get(signup.user_id)
    user = store.get_user(signup.user_id)

    assert response.status_code == 200
    assert state is not None
    assert state["billing_period"] == "annual"
    assert user is not None and user["plan_id"] == "pro"
    body = response.body.decode("utf-8")
    assert "annual" in body
    assert "£99.00/year" in body


def test_owner_activation_rejects_plan_tampering_against_approved_request(monkeypatch, tmp_path):
    store, preferences = _install(monkeypatch, tmp_path)
    signup = _annual_approved(store, preferences)

    response = admin_portal.owner_activate_payment(
        _request("/owner/activate-payment"),
        user_id=signup.user_id,
        plan_id="base",
        payment_reference="TAMPERED-PLAN-REF",
    )
    assert b"Requested membership plan does not match the activation plan" in response.body
    assert admin_portal.subscriptions.get(signup.user_id) is None

from __future__ import annotations

import aura_music_studio.member_dashboard as dashboard
from aura_music_studio.native_products import AURA_SEC_ENTITLEMENT


class _Access:
    def __init__(self, *, active: bool, sources: tuple[str, ...] = ()):
        self.active = active
        self.sources = sources

    def has(self, entitlement: str) -> bool:
        assert entitlement == AURA_SEC_ENTITLEMENT
        return self.active

    def sources_for(self, entitlement: str) -> tuple[str, ...]:
        assert entitlement == AURA_SEC_ENTITLEMENT
        return self.sources


def test_aura_sec_panel_reports_unlimited_pro_commercial_source_without_device_authority():
    html = dashboard._aura_sec_security_panel(
        _Access(active=True, sources=("membership:pro",))
    )

    assert "Aura Sec commercial access is active via Unlimited Pro membership" in html
    assert "Commercial access never grants native device trust by itself" in html
    assert "/aura-sec" in html
    assert "/account/native-products" in html
    assert "separate from ordinary creative membership" not in html


def test_aura_sec_panel_reports_verified_native_purchase_source():
    html = dashboard._aura_sec_security_panel(
        _Access(active=True, sources=("native_purchase",))
    )

    assert "Aura Sec commercial access is active via verified native purchase" in html
    assert "submit native heartbeat proof" in html
    assert "access command-signing keys" in html


def test_aura_sec_panel_reports_both_sources_without_duplicate_entitlement_claim():
    html = dashboard._aura_sec_security_panel(
        _Access(active=True, sources=("membership:pro", "native_purchase"))
    )

    assert "Unlimited Pro membership and verified native purchase" in html
    assert html.count("Aura Sec commercial access is active via") == 1


def test_aura_sec_panel_inactive_state_points_to_canonical_native_products_account():
    html = dashboard._aura_sec_security_panel(_Access(active=False))

    assert "Aura Sec is included with Unlimited Pro" in html
    assert "can also be purchased separately where offered" in html
    assert "Manage Aura OS &amp; Aura Sec" in html
    assert "/account/native-products" in html
    assert "Commercial access never grants native device trust by itself" in html
    assert "separate from ordinary creative membership" not in html


def test_dashboard_native_access_fails_closed_when_authoritative_account_is_missing(monkeypatch):
    def missing_account(_user_id: str):
        raise LookupError("Member account not found")

    monkeypatch.setattr(dashboard.native_access, "resolve", missing_account)

    access = dashboard._dashboard_native_access({"id": "synthetic-user", "plan_id": "pro"})

    assert access.user_id == "synthetic-user"
    assert access.membership_plan_id == "free"
    assert access.entitlements == frozenset()
    assert not access.has(AURA_SEC_ENTITLEMENT)
    assert access.sources_for(AURA_SEC_ENTITLEMENT) == ()


def test_dashboard_native_access_delegates_to_authoritative_resolver(monkeypatch):
    expected = _Access(active=True, sources=("membership:pro",))
    seen: list[str] = []

    def resolved(user_id: str):
        seen.append(user_id)
        return expected

    monkeypatch.setattr(dashboard.native_access, "resolve", resolved)

    assert dashboard._dashboard_native_access({"id": "member-123", "plan_id": "free"}) is expected
    assert seen == ["member-123"]

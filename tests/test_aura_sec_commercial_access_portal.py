from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import aura_music_studio.aura_sec_portal as portal
from aura_music_studio.native_products import AURA_SEC_ENTITLEMENT


class _Rows:
    def fetchall(self):
        return []


class _Connection:
    def execute(self, *_args, **_kwargs):
        return _Rows()


class _Security:
    def licence(self, _user_id: str) -> dict:
        return {"status": "inactive", "device_limit": None}

    def list_devices(self, _user_id: str) -> list[dict]:
        return []

    @contextmanager
    def _connect(self):
        yield _Connection()


class _Access:
    def __init__(self, snapshot: dict):
        self.snapshot = snapshot

    def resolve(self, _user_id: str):
        return SimpleNamespace(public_dict=lambda: self.snapshot)


def _commercial(*sources: str) -> dict:
    # Membership plan identity is intentionally independent from the standalone SLS
    # entitlement ledger. Only verified native billing/licensing evidence may add SLS.
    purchased = "native_purchase" in sources
    entitlements = [AURA_SEC_ENTITLEMENT] if purchased else []
    return {
        "user_id": "member-1",
        "membership_plan_id": "pro" if "membership:pro" in sources else "free",
        "entitlements": entitlements,
        "membership_entitlements": [],
        "purchased_entitlements": entitlements,
        "sources": {AURA_SEC_ENTITLEMENT: ["native_purchase"]} if purchased else {},
        "device_authority_granted": False,
        "device_limit": None,
    }


def test_unlimited_pro_without_standalone_sls_entitlement_is_not_active():
    status, copy = portal._aura_sec_access_label(_commercial("membership:pro"))

    assert status == "Not active"
    assert "verified standalone native entitlement" in copy


def test_aura_sec_access_label_reports_verified_native_entitlement():
    status, copy = portal._aura_sec_access_label(_commercial("native_purchase"))

    assert status == "Verified native entitlement"
    assert "verified native billing/licensing evidence" in copy


def test_aura_sec_snapshot_keeps_commercial_access_separate_from_native_device_authority(monkeypatch):
    monkeypatch.setattr(portal, "security", _Security())
    monkeypatch.setattr(portal, "native_access", _Access(_commercial("membership:pro")))

    snapshot = portal._safe_control_plane_snapshot("member-1")

    assert snapshot["commercial_access"]["entitlements"] == []
    assert snapshot["commercial_access"]["device_authority_granted"] is False
    assert snapshot["commercial_access"]["device_limit"] is None
    assert snapshot["device_licence"]["status"] == "inactive"
    assert snapshot["trust_boundary"]["commercial_entitlement_can_come_from_unlimited_pro"] is False
    assert snapshot["trust_boundary"]["commercial_entitlement_can_come_from_verified_native_purchase"] is True
    assert snapshot["trust_boundary"]["sls_native_licensing_separate_from_membership"] is True
    assert snapshot["trust_boundary"]["commercial_entitlement_grants_device_authority"] is False
    assert snapshot["trust_boundary"]["native_device_policy_separate_from_commercial_entitlement"] is True
    assert snapshot["trust_boundary"]["browser_can_execute_native_actions"] is False


def test_aura_sec_access_label_is_not_active_without_verified_native_entitlement():
    status, copy = portal._aura_sec_access_label(_commercial())

    assert status == "Not active"
    assert "verified standalone native entitlement" in copy

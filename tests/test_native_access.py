from __future__ import annotations

import pytest

from aura_music_studio.native_access import NativeAccessResolver
from aura_music_studio.native_products import AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT


class _Accounts:
    def __init__(self, user: dict | None):
        self.user = user
        self.db_path = ":memory:"

    def get_user(self, user_id: str):
        if self.user and self.user.get("id") == user_id:
            return dict(self.user)
        return None


class _Subscriptions:
    def __init__(self, enforced: dict | None = None):
        self.enforced = enforced
        self.calls: list[str] = []

    def enforce(self, user: dict):
        self.calls.append(user["id"])
        return dict(self.enforced) if self.enforced is not None else dict(user)


class _NativeLedger:
    def __init__(self, entitlements=()):
        self.entitlements = frozenset(entitlements)
        self.calls: list[str] = []

    def active_entitlements(self, user_id: str):
        self.calls.append(user_id)
        return self.entitlements


def _resolver(user: dict, *, purchased=(), enforced: dict | None = None):
    accounts = _Accounts(user)
    native = _NativeLedger(purchased)
    subscriptions = _Subscriptions(enforced)
    resolver = NativeAccessResolver(
        accounts=accounts,
        native_entitlements=native,
        subscriptions=subscriptions,
    )
    return resolver, native, subscriptions


def test_unlimited_pro_grants_both_native_entitlements_without_fabricating_device_authority():
    resolver, native, subscriptions = _resolver(
        {"id": "user-1", "status": "active", "plan_id": "pro"}
    )

    access = resolver.resolve("user-1")

    assert access.entitlements == {AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT}
    assert access.membership_entitlements == {AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT}
    assert access.purchased_entitlements == frozenset()
    assert access.sources_for(AURA_OS_ENTITLEMENT) == ("membership:pro",)
    assert access.sources_for(AURA_SEC_ENTITLEMENT) == ("membership:pro",)
    assert subscriptions.calls == ["user-1"]
    assert native.calls == ["user-1"]

    public = access.public_dict()
    assert public["device_authority_granted"] is False
    assert public["device_limit"] is None


def test_verified_native_purchase_can_grant_aura_sec_to_member_without_granting_aura_os():
    resolver, _, _ = _resolver(
        {"id": "user-2", "status": "active", "plan_id": "base"},
        purchased={AURA_SEC_ENTITLEMENT},
    )

    access = resolver.resolve("user-2")

    assert access.membership_entitlements == frozenset()
    assert access.purchased_entitlements == {AURA_SEC_ENTITLEMENT}
    assert access.has(AURA_SEC_ENTITLEMENT) is True
    assert access.has(AURA_OS_ENTITLEMENT) is False
    assert access.sources_for(AURA_SEC_ENTITLEMENT) == ("native_purchase",)


def test_bundle_or_standalone_purchase_and_pro_membership_sources_remain_distinguishable():
    resolver, _, _ = _resolver(
        {"id": "user-3", "status": "active", "plan_id": "pro"},
        purchased={AURA_SEC_ENTITLEMENT},
    )

    access = resolver.resolve("user-3")

    assert access.entitlements == {AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT}
    assert access.sources_for(AURA_OS_ENTITLEMENT) == ("membership:pro",)
    assert access.sources_for(AURA_SEC_ENTITLEMENT) == ("membership:pro", "native_purchase")


def test_subscription_expiry_enforcement_removes_pro_grant_but_keeps_verified_native_purchase():
    original = {"id": "user-4", "status": "active", "plan_id": "pro"}
    expired = {"id": "user-4", "status": "approved_pending_payment", "plan_id": "free"}
    resolver, _, _ = _resolver(
        original,
        purchased={AURA_SEC_ENTITLEMENT},
        enforced=expired,
    )

    access = resolver.resolve("user-4")

    assert access.membership_plan_id == "free"
    assert access.membership_entitlements == frozenset()
    assert access.purchased_entitlements == {AURA_SEC_ENTITLEMENT}
    assert access.entitlements == {AURA_SEC_ENTITLEMENT}


def test_non_native_ledger_values_cannot_expand_commercial_native_authority():
    resolver, _, _ = _resolver(
        {"id": "user-5", "status": "active", "plan_id": "base"},
        purchased={AURA_SEC_ENTITLEMENT, "esp_owner", "arbitrary_device_admin"},
    )

    access = resolver.resolve("user-5")

    assert access.entitlements == {AURA_SEC_ENTITLEMENT}
    assert "esp_owner" not in access.public_dict()["entitlements"]
    assert "arbitrary_device_admin" not in access.public_dict()["entitlements"]


def test_missing_account_fails_closed():
    resolver = NativeAccessResolver(
        accounts=_Accounts(None),
        native_entitlements=_NativeLedger(),
        subscriptions=_Subscriptions(),
    )

    with pytest.raises(LookupError, match="Member account not found"):
        resolver.resolve("missing-user")

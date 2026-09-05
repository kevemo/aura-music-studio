from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .accounts import AccountStore
from .native_billing import NativeEntitlementLedger
from .native_products import AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT
from .plans import get_plan
from .subscriptions import SubscriptionLedger

_NATIVE_ENTITLEMENTS = frozenset({AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT})


class NativeEntitlementSource(Protocol):
    def active_entitlements(self, user_id: str) -> frozenset[str]: ...


@dataclass(frozen=True)
class EffectiveNativeAccess:
    """Commercial native-product access only; never device or privileged-action authority.

    This snapshot intentionally carries no device limit, trust state, signing authority,
    heartbeat proof, command capability or re-authentication result. Those remain separate
    Aura Sec native-security boundaries.
    """

    user_id: str
    membership_plan_id: str
    membership_entitlements: frozenset[str]
    purchased_entitlements: frozenset[str]

    @property
    def entitlements(self) -> frozenset[str]:
        return self.membership_entitlements | self.purchased_entitlements

    def has(self, entitlement: str) -> bool:
        return entitlement in self.entitlements

    def sources_for(self, entitlement: str) -> tuple[str, ...]:
        sources: list[str] = []
        if entitlement in self.membership_entitlements:
            sources.append(f"membership:{self.membership_plan_id}")
        if entitlement in self.purchased_entitlements:
            sources.append("native_purchase")
        return tuple(sources)

    def public_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "membership_plan_id": self.membership_plan_id,
            "entitlements": sorted(self.entitlements),
            "membership_entitlements": sorted(self.membership_entitlements),
            "purchased_entitlements": sorted(self.purchased_entitlements),
            "sources": {
                entitlement: list(self.sources_for(entitlement))
                for entitlement in sorted(self.entitlements)
            },
            "device_authority_granted": False,
            "device_limit": None,
        }


class NativeAccessResolver:
    """Unify active Command Center and verified native-product commercial grants.

    Subscription expiry is enforced before plan features are considered. Verified native
    purchases remain lifecycle-aware through ``NativeEntitlementLedger.active_entitlements``.
    The resolver does not mutate either billing system and cannot create a native licence.
    """

    def __init__(
        self,
        accounts: AccountStore | None = None,
        native_entitlements: NativeEntitlementSource | None = None,
        subscriptions: SubscriptionLedger | None = None,
    ):
        self.accounts = accounts or AccountStore()
        self.native_entitlements = native_entitlements or NativeEntitlementLedger(self.accounts.db_path)
        self.subscriptions = subscriptions or SubscriptionLedger(self.accounts)

    def resolve(self, user_id: str) -> EffectiveNativeAccess:
        user = self.accounts.get_user(user_id)
        if not user:
            raise LookupError("Member account not found")

        enforced = self.subscriptions.enforce(user) or user
        status = str(enforced.get("status") or "").strip().lower()
        plan_id = str(enforced.get("plan_id") or "free").strip().lower()

        membership_entitlements: frozenset[str] = frozenset()
        if status in {"active", "owner"}:
            try:
                plan = get_plan(plan_id)
            except ValueError:
                plan = get_plan("free")
                plan_id = plan.id
            membership_entitlements = frozenset(plan.features & _NATIVE_ENTITLEMENTS)

        purchased = frozenset(
            entitlement
            for entitlement in self.native_entitlements.active_entitlements(str(enforced["id"]))
            if entitlement in _NATIVE_ENTITLEMENTS
        )

        return EffectiveNativeAccess(
            user_id=str(enforced["id"]),
            membership_plan_id=plan_id,
            membership_entitlements=membership_entitlements,
            purchased_entitlements=purchased,
        )


__all__ = ["EffectiveNativeAccess", "NativeAccessResolver"]

from __future__ import annotations

from copy import deepcopy
from typing import Final

from .native_products import public_native_products
from .plans import public_plans


CATALOGUE_SCHEMA_VERSION: Final[int] = 1
CATALOGUE_CURRENCY: Final[str] = "GBP"

# Stable internal membership IDs are compatibility identifiers. Public catalogue labels are
# deliberately owned here so presentation naming can evolve without mutating entitlement IDs or
# internal billing/session contracts that still use the historical Plan.name value.
PUBLIC_MEMBERSHIP_NAMES: Final[dict[str, str]] = {
    "free": "Free",
    "base": "Basic",
    "pro": "Unlimited Pro",
}

# Commercial product entitlement must never be allowed to become an ESP/owner authority grant.
# These are organisational/security authorities, not purchasable catalogue capabilities.
FORBIDDEN_PURCHASABLE_AUTHORITIES: Final[frozenset[str]] = frozenset(
    {
        "admin",
        "owner",
        "creator",
        "agent",
        "esp_member",
        "esp_admin",
        "protected_data_authority",
        "aura_sec_privileged_command",
    }
)


def _normalise_public_memberships(memberships: list[dict]) -> list[dict]:
    projected: list[dict] = []
    for item in memberships:
        public_item = deepcopy(item)
        plan_id = str(public_item.get("id") or "")
        public_name = PUBLIC_MEMBERSHIP_NAMES.get(plan_id)
        if public_name is None:
            raise RuntimeError(f"Unknown membership in public commercial catalogue: {plan_id}")
        public_item["name"] = public_name
        if plan_id == "free":
            description = str(public_item.get("description") or "")
            public_item["description"] = description.replace(
                "Music/video downloads and game creation unlock on Member.",
                "Music/video downloads and game creation unlock on Basic.",
            )
        elif plan_id == "base":
            description = str(public_item.get("description") or "")
            public_item["description"] = description.replace("£4.99 Member tier", "£4.99 Basic tier")
        projected.append(public_item)
    return projected


def _assert_catalogue_boundaries(memberships: list[dict], native_products: list[dict]) -> None:
    for item in memberships:
        if item.get("currency") != CATALOGUE_CURRENCY:
            raise RuntimeError(f"Membership {item.get('id')} is not priced in {CATALOGUE_CURRENCY}")
        features = set(item.get("features") or ())
        forbidden = features & FORBIDDEN_PURCHASABLE_AUTHORITIES
        if forbidden:
            raise RuntimeError(
                f"Membership {item.get('id')} contains forbidden purchasable authority: {sorted(forbidden)}"
            )

    for item in native_products:
        if item.get("currency") != CATALOGUE_CURRENCY:
            raise RuntimeError(f"Native product {item.get('id')} is not priced in {CATALOGUE_CURRENCY}")
        entitlements = set(item.get("entitlements") or ())
        forbidden = entitlements & FORBIDDEN_PURCHASABLE_AUTHORITIES
        if forbidden:
            raise RuntimeError(
                f"Native product {item.get('id')} contains forbidden purchasable authority: {sorted(forbidden)}"
            )


def public_commercial_catalogue() -> dict:
    """Return the single public commercial catalogue projection.

    Prices and entitlements are derived from the authoritative membership and native-product
    catalogues. Public labels are projected from stable IDs without changing internal entitlement
    or billing identifiers. This module deliberately owns no independent price constants and grants
    no organisational role. Callers receive a defensive copy so presentation code cannot mutate
    process-global catalogue state.
    """

    memberships = _normalise_public_memberships(public_plans())
    native_products = public_native_products()
    _assert_catalogue_boundaries(memberships, native_products)
    payload = {
        "schema_version": CATALOGUE_SCHEMA_VERSION,
        "currency": CATALOGUE_CURRENCY,
        "memberships": memberships,
        "native_products": native_products,
    }
    return deepcopy(payload)

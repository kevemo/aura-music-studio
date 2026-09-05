from __future__ import annotations

from copy import deepcopy

import pytest

from aura_music_studio.commercial_catalogue import (
    FORBIDDEN_PURCHASABLE_AUTHORITIES,
    _assert_catalogue_boundaries,
    public_commercial_catalogue,
)


def _by_id(items: list[dict]) -> dict[str, dict]:
    return {item["id"]: item for item in items}


def test_public_commercial_catalogue_matches_canonical_prices_and_periods():
    catalogue = public_commercial_catalogue()
    memberships = _by_id(catalogue["memberships"])
    native = _by_id(catalogue["native_products"])

    assert catalogue["schema_version"] == 1
    assert catalogue["currency"] == "GBP"

    assert memberships["base"]["monthly_price_minor"] == 499
    assert memberships["base"]["annual_price_minor"] is None
    assert memberships["base"]["supported_billing_periods"] == ["monthly"]

    assert memberships["pro"]["monthly_price_minor"] == 999
    assert memberships["pro"]["annual_price_minor"] == 9900
    assert memberships["pro"]["supported_billing_periods"] == ["monthly", "annual"]

    assert native["aura_os"]["monthly_price_minor"] == 499
    assert native["aura_os"]["annual_price_minor"] == 4999
    assert native["aura_sec"]["monthly_price_minor"] == 499
    assert native["aura_sec"]["annual_price_minor"] == 3499
    assert native["aura_sec"]["founding_first_year_price_minor"] == 2499
    assert native["aura_sec"]["founding_offer_renews_at_annual_price"] is True
    assert native["aura_os_sec_bundle"]["monthly_price_minor"] == 799
    assert native["aura_os_sec_bundle"]["annual_price_minor"] == 6999


def test_commercial_catalogue_preserves_role_and_security_authority_boundaries():
    catalogue = public_commercial_catalogue()

    for membership in catalogue["memberships"]:
        assert not (set(membership["features"]) & FORBIDDEN_PURCHASABLE_AUTHORITIES)

    native = _by_id(catalogue["native_products"])
    assert set(native["aura_os"]["entitlements"]) == {"aura_os"}
    assert set(native["aura_sec"]["entitlements"]) == {"aura_sec"}
    assert set(native["aura_os_sec_bundle"]["entitlements"]) == {"aura_os", "aura_sec"}
    for product in native.values():
        assert not (set(product["entitlements"]) & FORBIDDEN_PURCHASABLE_AUTHORITIES)


def test_commercial_catalogue_fails_closed_if_authority_is_inserted():
    memberships = deepcopy(public_commercial_catalogue()["memberships"])
    native = deepcopy(public_commercial_catalogue()["native_products"])
    memberships[0]["features"].append("admin")

    with pytest.raises(RuntimeError, match="forbidden purchasable authority"):
        _assert_catalogue_boundaries(memberships, native)


def test_public_catalogue_is_a_defensive_projection():
    first = public_commercial_catalogue()
    first["memberships"][0]["name"] = "tampered"
    first["native_products"][0]["entitlements"].append("owner")

    second = public_commercial_catalogue()
    assert second["memberships"][0]["name"] != "tampered"
    assert "owner" not in second["native_products"][0]["entitlements"]

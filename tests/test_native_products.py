from decimal import Decimal

import pytest

from aura_music_studio.native_products import (
    AURA_OS_ENTITLEMENT,
    AURA_SEC_ENTITLEMENT,
    BillingPeriod,
    get_native_product,
    public_native_products,
    require_native_entitlement,
)


def test_canonical_native_product_prices_match_release_gate():
    aura_os = get_native_product("aura_os")
    aura_sec = get_native_product("aura_sec")
    bundle = get_native_product("aura_os_sec_bundle")

    assert aura_os.price_for(BillingPeriod.MONTHLY) == Decimal("4.99")
    assert aura_os.price_for(BillingPeriod.ANNUAL) == Decimal("49.99")
    assert aura_sec.price_for(BillingPeriod.MONTHLY) == Decimal("4.99")
    assert aura_sec.price_for(BillingPeriod.ANNUAL) == Decimal("34.99")
    assert bundle.price_for(BillingPeriod.MONTHLY) == Decimal("7.99")
    assert bundle.price_for(BillingPeriod.ANNUAL) == Decimal("69.99")


def test_canonical_minor_units_are_deterministic():
    assert get_native_product("aura_os").price_minor_for("monthly") == 499
    assert get_native_product("aura_os").price_minor_for("annual") == 4999
    assert get_native_product("aura_sec").price_minor_for("annual") == 3499
    assert get_native_product("aura_os_sec_bundle").price_minor_for("monthly") == 799
    assert get_native_product("aura_os_sec_bundle").price_minor_for("annual") == 6999


def test_aura_sec_founding_offer_is_first_year_only_and_never_the_renewal_price():
    aura_sec = get_native_product("aura_sec")

    assert aura_sec.price_for("annual", founding_offer=True) == Decimal("24.99")
    assert aura_sec.price_minor_for("annual", founding_offer=True) == 2499
    assert aura_sec.renewal_price_for("annual") == Decimal("34.99")

    with pytest.raises(ValueError):
        aura_sec.price_for("monthly", founding_offer=True)


def test_founding_offer_cannot_be_applied_to_products_without_one():
    with pytest.raises(ValueError):
        get_native_product("aura_os").price_for("annual", founding_offer=True)

    with pytest.raises(ValueError):
        get_native_product("aura_os_sec_bundle").price_for("annual", founding_offer=True)


def test_unknown_products_and_periods_fail_closed():
    with pytest.raises(ValueError):
        get_native_product("unknown")

    with pytest.raises(ValueError):
        get_native_product("aura_os").price_for("weekly")


def test_native_product_entitlements_do_not_widen_role_or_creative_plan_authority():
    aura_os = get_native_product("aura_os")
    aura_sec = get_native_product("aura_sec")
    bundle = get_native_product("aura_os_sec_bundle")

    assert aura_os.entitlements == frozenset({AURA_OS_ENTITLEMENT})
    assert aura_sec.entitlements == frozenset({AURA_SEC_ENTITLEMENT})
    assert bundle.entitlements == frozenset({AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT})

    forbidden_authority = {
        "creator",
        "agent",
        "admin",
        "owner",
        "esp_member",
        "protected_data_authority",
        "aura_sec_privileged_command",
        "free",
        "base",
        "pro",
    }
    for product in (aura_os, aura_sec, bundle):
        assert product.entitlements.isdisjoint(forbidden_authority)


def test_native_entitlement_checks_are_product_scoped():
    require_native_entitlement("aura_os", AURA_OS_ENTITLEMENT)
    require_native_entitlement("aura_sec", AURA_SEC_ENTITLEMENT)
    require_native_entitlement("aura_os_sec_bundle", AURA_OS_ENTITLEMENT)
    require_native_entitlement("aura_os_sec_bundle", AURA_SEC_ENTITLEMENT)

    with pytest.raises(PermissionError):
        require_native_entitlement("aura_os", AURA_SEC_ENTITLEMENT)

    with pytest.raises(PermissionError):
        require_native_entitlement("aura_sec", AURA_OS_ENTITLEMENT)


def test_public_catalogue_marks_founding_offer_as_renewing_at_standard_annual_price():
    products = {product["id"]: product for product in public_native_products()}

    assert products["aura_sec"]["founding_first_year_price"] == "24.99"
    assert products["aura_sec"]["founding_first_year_price_minor"] == 2499
    assert products["aura_sec"]["founding_offer_renews_at_annual_price"] is True
    assert products["aura_sec"]["annual_price"] == "34.99"
    assert "founding_first_year_price_minor" not in products["aura_os"]

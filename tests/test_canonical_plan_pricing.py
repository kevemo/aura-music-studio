from decimal import Decimal

import pytest

from aura_music_studio.plans import get_plan, public_plans


def test_unlimited_pro_uses_canonical_monthly_and_annual_prices():
    pro = get_plan("pro")
    assert pro.currency == "GBP"
    assert pro.price_for("monthly") == Decimal("9.99")
    assert pro.price_for("annual") == Decimal("99.00")
    assert pro.price_minor_for("monthly") == 999
    assert pro.price_minor_for("annual") == 9900


def test_public_catalogue_exposes_same_canonical_pro_prices():
    by_id = {plan["id"]: plan for plan in public_plans()}
    assert by_id["pro"]["monthly_price"] == "9.99"
    assert by_id["pro"]["monthly_price_minor"] == 999
    assert by_id["pro"]["annual_price"] == "99.00"
    assert by_id["pro"]["annual_price_minor"] == 9900


def test_period_lookup_fails_closed_for_unknown_period():
    with pytest.raises(ValueError, match="Unsupported billing period"):
        get_plan("pro").price_for("weekly")


def test_annual_billing_is_not_invented_for_tier_two():
    base = get_plan("base")
    assert base.annual_price is None
    with pytest.raises(ValueError, match="Annual billing is not available"):
        base.price_for("annual")


def test_pricing_change_does_not_change_entitlement_boundaries():
    pro = get_plan("pro")
    base = get_plan("base")
    free = get_plan("free")
    assert pro.features > base.features > free.features

from decimal import Decimal

import pytest

from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.plans import AURASEC, AURA_OS, get_plan, public_plans


def test_unlimited_pro_uses_current_canonical_monthly_and_annual_prices():
    pro = get_plan("pro")

    assert pro.currency == "GBP"
    assert pro.price_for(BillingPeriod.MONTHLY) == Decimal("9.99")
    assert pro.price_for(BillingPeriod.ANNUAL) == Decimal("99.00")
    assert pro.price_minor_for("monthly") == 999
    assert pro.price_minor_for("annual") == 9900
    assert pro.display_price_for("monthly") == "£9.99/month"
    assert pro.display_price_for("annual") == "£99.00/year"


def test_public_catalogue_exposes_same_pro_prices_and_periods():
    by_id = {plan["id"]: plan for plan in public_plans()}

    assert by_id["pro"]["monthly_price"] == "9.99"
    assert by_id["pro"]["monthly_price_minor"] == 999
    assert by_id["pro"]["annual_price"] == "99.00"
    assert by_id["pro"]["annual_price_minor"] == 9900
    assert by_id["pro"]["supported_billing_periods"] == ["monthly", "annual"]


def test_basic_current_price_is_preserved_and_annual_is_not_invented():
    basic = get_plan("base")

    assert basic.id == "base"
    assert basic.name == "Basic"
    assert basic.monthly_price == Decimal("4.99")
    assert basic.monthly_price_minor == 499
    assert basic.annual_price is None
    assert basic.public_dict()["supported_billing_periods"] == ["monthly"]
    with pytest.raises(ValueError, match="Annual billing is not available"):
        basic.price_for("annual")


def test_period_lookup_fails_closed_for_unknown_period():
    with pytest.raises(ValueError, match="Unsupported billing period"):
        get_plan("pro").price_for("weekly")


def test_pricing_period_support_does_not_remove_pro_native_entitlements():
    pro = get_plan("pro")
    basic = get_plan("base")
    free = get_plan("free")

    assert pro.features > basic.features > free.features
    assert AURA_OS in pro.features
    assert AURASEC in pro.features
    assert AURA_OS not in basic.features
    assert AURASEC not in basic.features

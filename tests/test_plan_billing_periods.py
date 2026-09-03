from decimal import Decimal

import pytest

from aura_music_studio.native_products import BillingPeriod
from aura_music_studio.plans import get_plan, public_plans


def test_unlimited_pro_uses_canonical_monthly_and_annual_prices():
    pro = get_plan("pro")

    assert pro.price_for(BillingPeriod.MONTHLY) == Decimal("9.99")
    assert pro.price_for(BillingPeriod.ANNUAL) == Decimal("99.00")
    assert pro.price_minor_for("monthly") == 999
    assert pro.price_minor_for("annual") == 9900
    assert pro.display_price_for("monthly") == "£9.99/month"
    assert pro.display_price_for("annual") == "£99.00/year"


def test_tier_two_does_not_invent_an_annual_price():
    base = get_plan("base")

    assert base.price_for("monthly") == Decimal("5.99")
    assert base.annual_price is None
    assert base.annual_price_minor is None
    with pytest.raises(ValueError, match="Annual billing is not configured"):
        base.price_for("annual")


def test_free_plan_is_explicitly_zero_for_both_periods():
    free = get_plan("free")

    assert free.price_for("monthly") == Decimal("0.00")
    assert free.price_for("annual") == Decimal("0.00")


def test_unknown_billing_period_fails_closed():
    with pytest.raises(ValueError, match="Unsupported billing period"):
        get_plan("pro").price_for("weekly")


def test_public_plan_catalogue_exposes_supported_periods_without_price_drift():
    plans = {row["id"]: row for row in public_plans()}

    assert plans["pro"]["monthly_price"] == "9.99"
    assert plans["pro"]["monthly_price_minor"] == 999
    assert plans["pro"]["annual_price"] == "99.00"
    assert plans["pro"]["annual_price_minor"] == 9900
    assert plans["pro"]["supported_billing_periods"] == ["monthly", "annual"]
    assert plans["base"]["supported_billing_periods"] == ["monthly"]

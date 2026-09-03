from __future__ import annotations

from decimal import Decimal

from aura_music_studio.plans import get_plan, public_plans


def test_canonical_public_membership_names_and_prices():
    plans = {plan["id"]: plan for plan in public_plans()}

    assert list(plans) == ["free", "base", "pro"]
    assert plans["free"]["name"] == "Free"
    assert plans["base"]["name"] == "Basic"
    assert plans["pro"]["name"] == "Unlimited Pro"
    assert plans["base"]["monthly_price"] == "4.99"
    assert plans["base"]["currency"] == "GBP"
    assert plans["pro"]["monthly_price"] == "9.99"
    assert plans["pro"]["annual_price"] == "99.00"


def test_basic_name_change_preserves_stable_internal_base_entitlement_id():
    basic = get_plan("base")

    assert basic.id == "base"
    assert basic.name == "Basic"
    assert basic.monthly_price == Decimal("4.99")

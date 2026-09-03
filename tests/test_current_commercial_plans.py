from decimal import Decimal

from aura_music_studio.plans import get_plan, public_plans


def test_current_public_membership_prices_are_gbp_and_use_authoritative_amounts():
    free = get_plan("free")
    tier_two = get_plan("base")
    pro = get_plan("pro")

    assert free.monthly_price == Decimal("0.00")
    assert tier_two.monthly_price == Decimal("5.99")
    assert pro.monthly_price == Decimal("9.99")
    assert pro.annual_price == Decimal("99.00")
    assert {free.currency, tier_two.currency, pro.currency} == {"GBP"}
    assert tier_two.monthly_price_minor == 599
    assert tier_two.annual_price_minor is None
    assert pro.monthly_price_minor == 999
    assert pro.annual_price_minor == 9900


def test_current_customer_facing_plan_names_and_prices_are_exposed_without_changing_stable_ids():
    plans = {plan["id"]: plan for plan in public_plans()}

    assert set(plans) == {"free", "base", "pro"}
    assert plans["free"]["name"] == "Free"
    assert plans["base"]["name"] == "Tier 2"
    assert plans["pro"]["name"] == "Unlimited Pro"
    assert plans["base"]["display_price"] == "£5.99"
    assert plans["base"]["display_annual_price"] is None
    assert plans["pro"]["display_price"] == "£9.99"
    assert plans["pro"]["display_annual_price"] == "£99.00"
    assert plans["base"]["monthly_price_minor"] == 599
    assert plans["pro"]["monthly_price_minor"] == 999
    assert plans["pro"]["annual_price_minor"] == 9900


def test_superseded_price_values_are_not_present_in_current_plan_descriptions():
    public_text = " ".join(
        f"{plan['name']} {plan['display_price']} {plan.get('display_annual_price') or ''} {plan['description']}"
        for plan in public_plans()
    )

    assert "£4.99" not in public_text
    assert "£14.99" not in public_text
    assert "£9.99" in public_text
    assert "£99.00" in public_text

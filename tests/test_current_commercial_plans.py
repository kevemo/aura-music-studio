from decimal import Decimal

from aura_music_studio.plans import get_plan, public_plans


def test_current_public_membership_prices_are_gbp_and_use_authoritative_amounts():
    free = get_plan("free")
    basic = get_plan("base")
    pro = get_plan("pro")

    assert free.monthly_price == Decimal("0.00")
    assert basic.monthly_price == Decimal("4.99")
    assert pro.monthly_price == Decimal("9.99")
    assert {free.currency, basic.currency, pro.currency} == {"GBP"}
    assert basic.monthly_price_minor == 499
    assert pro.monthly_price_minor == 999


def test_current_customer_facing_plan_names_and_prices_are_exposed_without_changing_stable_ids():
    plans = {plan["id"]: plan for plan in public_plans()}

    assert set(plans) == {"free", "base", "pro"}
    assert plans["free"]["name"] == "Free"
    assert plans["base"]["name"] == "Basic"
    assert plans["pro"]["name"] == "Unlimited Pro"
    assert plans["base"]["display_price"] == "£4.99"
    assert plans["pro"]["display_price"] == "£9.99"
    assert plans["base"]["monthly_price_minor"] == 499
    assert plans["pro"]["monthly_price_minor"] == 999


def test_superseded_price_values_are_not_present_in_current_plan_descriptions():
    public_text = " ".join(
        f"{plan['name']} {plan['display_price']} {plan['description']}" for plan in public_plans()
    )

    assert "£5.99" not in public_text
    assert "£14.99" not in public_text
    assert "£4.99" in public_text
    assert "£9.99" in public_text

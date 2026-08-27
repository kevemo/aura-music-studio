from aura_music_studio.plans import PLANS, public_plans


def test_public_tier_names_are_free_basic_pro_with_compatibility_id_preserved():
    assert list(PLANS) == ["free", "base", "pro"]
    assert [PLANS[key].name for key in ("free", "base", "pro")] == ["Free", "Basic", "Pro"]
    assert PLANS["base"].id == "base"

    # GBP is authoritative. The historical monthly_price_usd attribute remains a
    # temporary compatibility alias so parallel build branches do not break.
    assert [PLANS[key].currency for key in ("free", "base", "pro")] == ["GBP", "GBP", "GBP"]
    assert str(PLANS["free"].monthly_price) == "0.00"
    assert str(PLANS["base"].monthly_price) == "4.99"
    assert str(PLANS["pro"].monthly_price) == "9.99"
    assert PLANS["base"].monthly_price_minor == 499
    assert PLANS["pro"].monthly_price_minor == 999
    assert PLANS["base"].display_price == "£4.99"
    assert PLANS["pro"].display_price == "£9.99"
    assert str(PLANS["base"].monthly_price_usd) == "4.99"

    rows = public_plans()
    assert [row["name"] for row in rows] == ["Free", "Basic", "Pro"]
    assert rows[1]["currency"] == "GBP"
    assert rows[1]["monthly_price"] == "4.99"
    assert rows[1]["monthly_price_minor"] == 499
    assert rows[1]["display_price"] == "£4.99"
    assert rows[1]["monthly_price_usd"] == "4.99"


def test_subscription_entitlements_do_not_contain_esp_or_social_access_roles():
    for plan in PLANS.values():
        lowered = {feature.lower() for feature in plan.features}
        assert not any("esp" in feature for feature in lowered)
        assert not any("social" in feature for feature in lowered)
        assert not any("agent" in feature for feature in lowered)
        assert not any("creator_network" in feature for feature in lowered)

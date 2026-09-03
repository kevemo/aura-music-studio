from aura_music_studio.native_products import AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT
from aura_music_studio.plans import AURA_OS, AURASEC, PLANS, public_plans


def test_public_tier_names_match_current_spec_with_compatibility_id_preserved():
    assert list(PLANS) == ["free", "base", "pro"]
    assert [PLANS[key].name for key in ("free", "base", "pro")] == ["Free", "Member", "Unlimited Pro"]
    assert PLANS["base"].id == "base"

    # GBP is authoritative. The historical monthly_price_usd attribute remains a
    # temporary compatibility alias so parallel build branches do not break.
    assert [PLANS[key].currency for key in ("free", "base", "pro")] == ["GBP", "GBP", "GBP"]
    assert str(PLANS["free"].monthly_price) == "0.00"
    assert str(PLANS["base"].monthly_price) == "5.99"
    assert str(PLANS["base"].annual_price) == "59.99"
    assert str(PLANS["pro"].monthly_price) == "9.99"
    assert str(PLANS["pro"].annual_price) == "99.00"
    assert PLANS["base"].monthly_price_minor == 599
    assert PLANS["base"].annual_price_minor == 5999
    assert PLANS["pro"].monthly_price_minor == 999
    assert PLANS["pro"].annual_price_minor == 9900
    assert PLANS["base"].display_price == "£5.99"
    assert PLANS["pro"].display_price == "£9.99"
    assert str(PLANS["base"].monthly_price_usd) == "5.99"

    rows = public_plans()
    assert [row["name"] for row in rows] == ["Free", "Member", "Unlimited Pro"]
    assert rows[1]["currency"] == "GBP"
    assert rows[1]["monthly_price"] == "5.99"
    assert rows[1]["monthly_price_minor"] == 599
    assert rows[1]["annual_price"] == "59.99"
    assert rows[1]["annual_price_minor"] == 5999
    assert rows[1]["supported_billing_periods"] == ["monthly", "annual"]
    assert rows[1]["display_price"] == "£5.99"
    assert rows[1]["monthly_price_usd"] == "5.99"


def test_unlimited_pro_uses_canonical_native_aura_os_and_aura_sec_entitlements():
    assert AURA_OS == AURA_OS_ENTITLEMENT == "aura_os"
    assert AURASEC == AURA_SEC_ENTITLEMENT == "aura_sec"

    for entitlement in (AURA_OS, AURASEC):
        assert entitlement not in PLANS["free"].features
        assert entitlement not in PLANS["base"].features
        assert entitlement in PLANS["pro"].features

    public_pro = next(row for row in public_plans() if row["id"] == "pro")
    assert AURA_OS_ENTITLEMENT in public_pro["features"]
    assert AURA_SEC_ENTITLEMENT in public_pro["features"]


def test_subscription_entitlements_do_not_contain_esp_or_social_access_roles():
    for plan in PLANS.values():
        lowered = {feature.lower() for feature in plan.features}
        assert not any("esp" in feature for feature in lowered)
        assert not any("social" in feature for feature in lowered)
        assert not any("agent" in feature for feature in lowered)
        assert not any("creator_network" in feature for feature in lowered)

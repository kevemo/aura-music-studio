from pathlib import Path


def test_commercial_router_is_mounted_through_final_esp_overlay():
    source = Path("aura_music_studio/esp_agent_roster_overlay.py").read_text(encoding="utf-8")
    assert "esp_brand_opportunities" in source
    assert "router.include_router(brand_opportunities_router)" in source
    assert "/command-center/commercial-opportunities" in source
    assert "/command-center/api/level-up/capabilities" in source
    assert 'value.get("id") == "commerce-brands"' in source


def test_commercial_module_keeps_truthful_payment_and_consent_boundaries():
    source = Path("aura_music_studio/esp_brand_opportunities.py").read_text(encoding="utf-8")
    assert "tracking_only_no_money_transfer" in source
    assert '"money_transferred": False' in source
    assert "creator_opt_in_confirmed" in source
    assert "Owner-assisted application requires recorded creator opt-in" in source
    assert "Creator is not actively assigned to this agent" in source
    assert "final approve/reject is owner-controlled" in source

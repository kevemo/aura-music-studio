from __future__ import annotations

from aura_music_studio import creation_live as cl
from aura_music_studio.creation_live_hardening import install_creation_live_hardening


install_creation_live_hardening()


def test_creator_community_panel_polls_only_while_drawer_is_open_and_cleans_timer():
    script = cl.LIVE_UI_SCRIPT
    assert "function scheduleCommunity()" in script
    assert "setInterval" in script
    assert "5000" in script
    assert "drawer&&!drawer.hidden" in script
    assert "state.communityBusy" in script
    assert "clearInterval(state.communityTimer)" in script
    assert "state.communityTimer=null" in script


def test_attach_refreshes_community_before_transport_readiness_message():
    script = cl.LIVE_UI_SCRIPT
    assert "renderStatus();await community();const pf=data.transport_preflight||{};" in script
    assert "Programme remains NOT CONFIRMED ON AIR" in script


def test_creator_panel_exposes_gift_and_battle_display_truth_without_mutation():
    script = cl.LIVE_UI_SCRIPT
    assert "d.gift_display||{}" in script
    assert "d.battle_display||{}" in script
    assert "Gift display connected" in script
    assert "Battle display connected" in script
    assert "Community is display-only here" in script
    assert "body.textContent" in script
    assert "debit_wallet" not in script
    assert "score_battle" not in script

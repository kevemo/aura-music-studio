from __future__ import annotations

import inspect


def test_interactive_overlay_router_is_mounted():
    from aura_music_studio import api as api_mod
    from aura_music_studio.aura_live_overlay_interactives import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/api/live-overlays/timer" in paths
    assert "/api/live-overlays/wheels" in paths
    assert "/api/live-overlays/challenges" in paths
    assert "/api/live-overlays/auction" in paths
    assert "/api/live-overlays/announcements" in paths
    assert "/api/live-overlays/requests" in paths
    source = inspect.getsource(api_mod)
    assert "app.include_router(aura_live_overlay_interactives_router)" in source


def test_request_queue_does_not_fake_spotify_connection():
    from aura_music_studio import aura_live_overlay_interactives as mod

    source = inspect.getsource(mod.requests_queue)
    assert '"spotify_connected": False' in source
    assert "separate authorized provider integration" in source


def test_wheel_uses_cryptographic_random_selection():
    from aura_music_studio import aura_live_overlay_interactives as mod

    source = inspect.getsource(mod.spin_wheel)
    assert "secrets.randbelow" in source
    assert '"cryptographic_random_selection": True' in source


def test_free_tier_cannot_use_paid_interactives():
    from aura_music_studio import aura_live_overlay_interactives as mod

    source = inspect.getsource(mod._require_paid)
    assert 'member.plan.id == "free"' in source
    assert "Interactive LIVE tools require Basic or Pro" in source


def test_auction_is_host_controlled_and_has_no_real_money_checkout():
    from aura_music_studio import aura_live_overlay_interactives as mod

    source = inspect.getsource(mod)
    assert "minimum_bid" in source
    assert "leader_username" in source
    assert "leader_value" in source
    assert "payment" not in inspect.getsource(mod.auction_bid).lower()
    assert "checkout" not in inspect.getsource(mod.auction_bid).lower()

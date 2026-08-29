from __future__ import annotations

import inspect


def test_personalisation_routes_are_mounted_through_connector_router():
    from aura_music_studio.aura_live_overlay_connector import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/api/live-overlays/gift-reactions" in paths
    assert "/api/live-overlays/top-streak" in paths
    assert "/api/live-overlays/rotator" in paths
    assert "/live-overlay/streak/{token}" in paths
    assert "/live-overlay/rotator/{token}" in paths


def test_per_gift_mapping_is_tenant_confined_and_does_not_fake_gift_catalog():
    from aura_music_studio import aura_live_overlay_personalization as mod

    source = inspect.getsource(mod.upsert_gift_reaction)
    assert "id=? AND user_id=?" in source
    listing = inspect.getsource(mod.list_gift_reactions)
    assert '"gift_catalog_connected": False' in listing
    assert "trusted provider supplies current TikTok gift metadata" in listing


def test_gift_event_drives_streak_and_saved_reaction(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as engine

    db = tmp_path / "gift.sqlite3"
    monkeypatch.setattr(engine, "DB_PATH", db)
    engine._init_schema()
    now = engine._now()
    with engine._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_gift_reactions(id,user_id,gift_name,min_count,visual,media_id,sound_media_id,tts_template,enabled,created_at,updated_at) VALUES(?,?,?,?,?,NULL,NULL,?,1,?,?)",
            ("r1", "u1", "Rose", 2, "spotlight", "Thank you {username} for {gift_count} {gift_name}s!", now, now),
        )
    result = engine.process_overlay_event("u1", "gift", {"username": "Laura", "gift_name": "Rose", "gift_count": 2, "coins": 2})
    assert result["gift_streak"]["username"] == "Laura"
    assert result["gift_streak"]["current_streak"] == 2
    assert result["gift_reaction"]["reaction_id"] == "r1"
    actions = result["gift_reaction"]["actions"]
    assert any(a["action"] == "spotlight_viewer" for a in actions)
    assert any(a["action"] == "speak" for a in actions)
    with engine._connect() as con:
        derived = con.execute("SELECT payload_json FROM live_overlay_events WHERE user_id='u1' AND event_type='custom'").fetchall()
    assert any("aura_gift_reaction" in row[0] for row in derived)


def test_streak_resets_after_configured_window(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as engine
    from datetime import datetime, timedelta, timezone

    db = tmp_path / "streak.sqlite3"
    monkeypatch.setattr(engine, "DB_PATH", db)
    engine._init_schema()
    old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    with engine._connect() as con:
        con.execute("INSERT INTO live_overlay_gift_streaks(user_id,username,current_streak,best_streak,last_gift_at,updated_at) VALUES(?,?,?,?,?,?)", ("u1", "Laura", 20, 20, old, old))
    result = engine.process_overlay_event("u1", "gift", {"username": "Laura", "gift_name": "Rose", "gift_count": 1, "coins": 1})
    assert result["gift_streak"]["current_streak"] == 1
    assert result["gift_streak"]["best_streak"] == 20


def test_rotator_source_is_single_browser_source_and_token_bound():
    from aura_music_studio import aura_live_overlay_personalization as mod

    source = inspect.getsource(mod.rotator_source)
    assert "/live-overlay/advanced/" in source
    assert "leaderboard" in source
    assert "goals" in source
    assert "announcements" in source
    assert "challenges" in source
    assert "auction" in source
    assert "eval(" not in source

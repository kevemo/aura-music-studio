from __future__ import annotations

import inspect
import json
from pathlib import Path


def test_advanced_overlay_routes_are_mounted_in_production_api():
    from aura_music_studio import api as api_mod
    from aura_music_studio.aura_live_overlay_advanced import router as advanced
    from aura_music_studio.aura_live_overlay_engine import router as engine

    advanced_paths = {getattr(route, "path", "") for route in advanced.routes}
    assert "/live-overlay-studio/editor" in advanced_paths
    assert "/live-overlay-studio/automations" in advanced_paths
    assert "/api/live-overlays/scenes" in advanced_paths
    assert "/api/live-overlays/rules" in advanced_paths
    assert "/api/live-overlays/goals" in advanced_paths
    assert "/api/live-overlays/leaderboards" in advanced_paths
    assert "/api/live-overlays/media" in advanced_paths
    engine_paths = {getattr(route, "path", "") for route in engine.routes}
    assert "/api/live-overlays/simulate" in engine_paths
    assert "/api/live-overlays/event-contract" in engine_paths
    source = inspect.getsource(api_mod)
    assert "app.include_router(aura_live_overlay_advanced_router)" in source
    assert "app.include_router(aura_live_overlay_engine_router)" in source


def test_tiers_bound_scene_rule_goal_media_and_widget_capacity():
    from aura_music_studio.aura_live_overlay_advanced import TIER_LIMITS

    assert TIER_LIMITS["free"] == {"rules": 5, "scenes": 1, "goals": 2, "media": 5, "widgets": 8, "advanced": False}
    assert TIER_LIMITS["base"]["rules"] == 30
    assert TIER_LIMITS["pro"]["rules"] == 250
    assert TIER_LIMITS["pro"]["widgets"] == 100


def test_automation_actions_are_allowlisted_and_never_shell_or_javascript():
    from aura_music_studio.aura_live_overlay_advanced import ACTIONS
    from aura_music_studio.aura_live_overlay_engine import SAFE_ACTIONS

    expected = {"show_widget", "play_media", "play_sound", "speak", "increment_goal", "add_timer_seconds", "spin_wheel", "switch_scene"}
    assert expected <= ACTIONS
    assert expected <= SAFE_ACTIONS
    forbidden = {"shell", "powershell", "javascript", "eval", "exec", "run_command", "open_url"}
    assert not (forbidden & ACTIONS)
    assert not (forbidden & SAFE_ACTIONS)


def test_event_engine_updates_stats_goals_and_emits_safe_automation(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as eng

    db = tmp_path / "overlay.sqlite3"
    monkeypatch.setattr(eng, "DB_PATH", db)
    eng._init_schema()
    now = eng._now()
    with eng._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_goals(id,user_id,name,metric,target,current,reset_mode,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,?,?)",
            ("goal-1", "u1", "Gift goal", "gift_value", 1000, 0, "per_live", now, now),
        )
        con.execute(
            "INSERT INTO live_overlay_rules(id,user_id,name,event_type,condition_json,actions_json,cooldown_seconds,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "rule-1", "u1", "Big Rose", "gift", json.dumps({"gift_name": "Rose", "min_coins": 10}),
                json.dumps([{"action": "speak", "params": {"text": "Thank you {username}!"}}]), 0, 1, now, now,
            ),
        )
    result = eng.process_overlay_event("u1", "gift", {"username": "Laura", "gift_name": "Rose", "gift_count": 2, "coins": 25})
    assert result["accepted"]
    assert result["goals_updated"][0]["current"] == 25
    assert result["rules_fired"][0]["rule_id"] == "rule-1"
    with eng._connect() as con:
        stat = con.execute("SELECT * FROM live_overlay_session_stats WHERE user_id='u1' AND username='Laura'").fetchone()
        assert stat["gift_count"] == 2
        assert stat["gift_value"] == 25
        events = con.execute("SELECT event_type,payload_json FROM live_overlay_events WHERE user_id='u1' ORDER BY id").fetchall()
        assert [r["event_type"] for r in events] == ["gift", "custom"]


def test_provider_contract_fails_closed_until_real_connector_is_validated():
    from aura_music_studio import aura_live_overlay_advanced as adv
    from aura_music_studio import aura_live_overlay_engine as eng

    assert '"provider_connection_claimed": False' in inspect.getsource(adv.capabilities)
    assert '"connected": False' in inspect.getsource(adv.provider_status)
    source = inspect.getsource(eng.event_contract)
    assert '"provider_connected": False' in source
    assert "trusted, maintainable TikTok LIVE event adapter" in source


def test_media_library_has_tenant_root_confinement_size_and_type_allowlist():
    from aura_music_studio import aura_live_overlay_advanced as adv

    source = inspect.getsource(adv.upload_media)
    assert "MAX_MEDIA_BYTES" in source
    assert "MEDIA_TYPES" in source
    assert "hashlib.sha256(member.user_id.encode())" in source
    read_source = inspect.getsource(adv.media_file)
    assert "user_root.resolve() not in target.parents" in read_source
    assert '"X-Content-Type-Options": "nosniff"' in read_source


def test_research_parity_event_contract_includes_modern_live_events():
    from aura_music_studio.aura_live_overlay_engine import EVENT_TYPES

    assert {"battle_start", "battle_progress", "battle_end", "poll", "treasure_chest", "question", "pinned_message", "live_shopping", "super_fan", "shared_stream", "chat_deleted"} <= EVENT_TYPES

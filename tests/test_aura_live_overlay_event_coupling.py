from __future__ import annotations


def test_normalized_events_advance_challenges_and_gift_scoreboard(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as engine

    db = tmp_path / "events.sqlite3"
    monkeypatch.setattr(engine, "DB_PATH", db)
    engine._init_schema()
    now = engine._now()
    with engine._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_challenges(id,user_id,name,event_type,gift_name,target,current,reward_text,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,1,?,?)",
            ("c1", "u1", "Five Roses", "gift", "Rose", 5, "Wheel spin unlocked", now, now),
        )
        con.execute(
            "INSERT INTO live_overlay_auction(user_id,title,active,minimum_bid,leader_username,leader_value,ends_at,updated_at) VALUES(?,?,1,?,NULL,0,NULL,?)",
            ("u1", "Gift scoreboard", 10, now),
        )

    result = engine.process_overlay_event(
        "u1", "gift", {"username": "Laura", "gift_name": "Rose", "gift_count": 5, "coins": 25}
    )
    assert result["challenges_updated"][0]["completed"] is True
    assert result["challenges_updated"][0]["current"] == 5
    assert result["auction_update"]["leader_changed"] is True
    assert result["auction_update"]["leader_username"] == "Laura"
    assert result["auction_update"]["payment_processed"] is False
    with engine._connect() as con:
        custom = [r[0] for r in con.execute("SELECT payload_json FROM live_overlay_events WHERE user_id='u1' AND event_type='custom'").fetchall()]
    assert any("challenge_completed" in row for row in custom)
    assert any("auction_leader" in row for row in custom)


def test_gift_challenge_requires_matching_gift_name(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_overlay_engine as engine

    db = tmp_path / "challenge.sqlite3"
    monkeypatch.setattr(engine, "DB_PATH", db)
    engine._init_schema()
    now = engine._now()
    with engine._connect() as con:
        con.execute(
            "INSERT INTO live_overlay_challenges(id,user_id,name,event_type,gift_name,target,current,reward_text,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,0,NULL,1,?,?)",
            ("c1", "u1", "Rose challenge", "gift", "Rose", 10, now, now),
        )
    result = engine.process_overlay_event("u1", "gift", {"username": "Viewer", "gift_name": "Galaxy", "gift_count": 10, "coins": 1000})
    assert result["challenges_updated"] == []


def test_live_reset_clears_challenge_and_auction_progress_source_contract():
    from aura_music_studio import aura_live_overlay_engine as engine
    import inspect

    source = inspect.getsource(engine.reset_session_stats)
    assert "UPDATE live_overlay_challenges SET current=0" in source
    assert "UPDATE live_overlay_auction SET leader_username=NULL,leader_value=0" in source

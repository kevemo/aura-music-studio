import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from aura_music_studio.shared_sky_battles import SharedSkyBattleStore
from aura_music_studio import shared_sky_live_battle_bridge as battle_bridge


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


def make_store(tmp_path):
    db = tmp_path / "battle-viewer.sqlite3"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE shared_sky_broadcasts (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,state TEXT NOT NULL)"
    )
    con.execute("INSERT INTO shared_sky_broadcasts VALUES ('live1','host','live')")
    con.commit()
    con.close()
    clock = Clock()
    store = SharedSkyBattleStore(
        db,
        clock=clock,
        transport_capacity=lambda _sid: 8,
        participant_eligibility=lambda _uid: (True, "eligible"),
        reconnect_grace_seconds=45,
    )
    return store, clock, db


def prepare_ready_battle(store):
    host = store.ensure_host("live1", "host")
    invite = store.create_invitation("live1", "host", "guest")
    guest = store.respond_invitation(
        invite["id"], "guest", invite_token=invite["invite_token"], accept=True
    )
    host = store.update_readiness(
        host["id"],
        "host",
        terms_accepted=True,
        camera_ready=True,
        microphone_ready=True,
        audio_available=True,
        video_available=True,
        connection_state="connected",
        media_ref="media:host",
    )
    guest = store.update_readiness(
        guest["id"],
        "guest",
        terms_accepted=True,
        camera_ready=True,
        microphone_ready=True,
        audio_available=True,
        video_available=True,
        connection_state="connected",
        media_ref="media:guest",
    )
    store.set_stage_state(host["id"], "host", "stage")
    store.set_stage_state(guest["id"], "host", "stage")
    ruleset = store.create_ruleset(
        "viewer-default",
        1,
        "Viewer Default",
        {
            "round_duration_seconds": 30,
            "rounds": 1,
            "late_event_grace_seconds": 5,
            "tie_policy": "declare_tie",
            "eligible_sources": {"gift": {"fixed_score": 1}},
        },
        "owner",
        activate=True,
        explanation="Viewer lookup regression rules",
    )
    battle = store.create_battle("live1", "host", ruleset["id"], mode="1v1")
    return battle, ruleset


def test_viewer_live_battle_returns_current_viewer_safe_snapshot(tmp_path):
    store, _clock, _db = make_store(tmp_path)
    battle, _ruleset = prepare_ready_battle(store)

    snapshot = store.viewer_live_battle("live1")

    assert snapshot is not None
    assert snapshot["battle"]["id"] == battle["battle"]["id"]
    assert snapshot["battle"]["status"] == "ready"
    assert snapshot["battle"]["live_session_id"] == "live1"
    assert {item["user_id"] for item in snapshot["participants"]} == {"host", "guest"}
    assert "created_by_user_id" not in snapshot["battle"]
    assert all("readiness_state" not in item for item in snapshot["participants"])


def test_viewer_live_battle_excludes_terminal_history_and_selects_new_current(tmp_path):
    store, clock, db = make_store(tmp_path)
    first, ruleset = prepare_ready_battle(store)
    first_id = first["battle"]["id"]

    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE shared_sky_battles SET status='completed',ended_at=?,updated_at=? WHERE id=?",
            (clock().isoformat(), clock().isoformat(), first_id),
        )

    assert store.viewer_live_battle("live1") is None
    assert store.viewer_live_battle("unrelated-live") is None

    second = store.create_battle("live1", "host", ruleset["id"], mode="1v1")
    snapshot = store.viewer_live_battle("live1")

    assert snapshot is not None
    assert snapshot["battle"]["id"] == second["battle"]["id"]
    assert snapshot["battle"]["id"] != first_id


def test_battle_display_adapter_uses_viewer_lookup_and_fails_closed_for_no_current(tmp_path):
    store, clock, db = make_store(tmp_path)
    battle, _ruleset = prepare_ready_battle(store)
    adapter = battle_bridge.Chat6BattleDisplayAdapter(store.viewer_live_battle)

    state = adapter.state("live1", None)
    assert state["available"] is True
    assert state["battle_id"] == battle["battle"]["id"]
    assert state["source"] == "chat6_viewer_live_battle"

    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE shared_sky_battles SET status='completed',ended_at=?,updated_at=? WHERE id=?",
            (clock().isoformat(), clock().isoformat(), battle["battle"]["id"]),
        )

    closed = adapter.state("live1", None)
    assert closed == {
        "available": False,
        "reason": "no_active_battle",
        "source": "chat6_viewer_bridge",
    }


def test_install_bridge_accepts_canonical_store_viewer_lookup(monkeypatch):
    def viewer_live_battle(live_session_id):
        return {"battle": {"id": "b1", "live_session_id": live_session_id}}

    fake_api = SimpleNamespace(
        battle_store=SimpleNamespace(viewer_live_battle=viewer_live_battle)
    )
    registered = {}

    monkeypatch.setattr(battle_bridge.importlib, "import_module", lambda _name: fake_api)
    monkeypatch.setattr(
        battle_bridge.live,
        "register_battle_display_adapter",
        lambda adapter: registered.setdefault("adapter", adapter),
    )

    result = battle_bridge.install_chat6_battle_viewer_bridge()

    assert result == {
        "state": "registered",
        "source": "shared_sky_battle_api.battle_store.viewer_live_battle",
        "authority": "chat6_read_only",
    }
    assert registered["adapter"].viewer_live_battle is viewer_live_battle

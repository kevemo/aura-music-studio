import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.shared_sky_battles import (
    BattleDomainError,
    CommittedGiftEvent,
    EngagementScoreEvent,
    ReversedGiftEvent,
    SharedSkyBattleStore,
)

class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 5, 1, 0, tzinfo=timezone.utc)
    def __call__(self):
        return self.value
    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)

@pytest.fixture
def env(tmp_path):
    db = tmp_path / "battle.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE shared_sky_broadcasts (id TEXT PRIMARY KEY,user_id TEXT NOT NULL,state TEXT NOT NULL)")
    con.execute("INSERT INTO shared_sky_broadcasts VALUES ('live1','host','live')")
    con.commit(); con.close()
    clock = Clock()
    store = SharedSkyBattleStore(db, clock=clock, transport_capacity=lambda _sid: 8, participant_eligibility=lambda _uid: (True, "eligible"), reconnect_grace_seconds=45)
    host = store.ensure_host("live1", "host")
    return store, clock, host, db

def invite_accept(store, user):
    invite = store.create_invitation("live1", "host", user)
    return store.respond_invitation(invite["id"], user, invite_token=invite["invite_token"], accept=True)

def mark_ready(store, participant, user):
    return store.update_readiness(
        participant["id"], user,
        terms_accepted=True, camera_ready=True, microphone_ready=True,
        audio_available=True, video_available=True, connection_state="connected",
        media_ref=f"media:{user}",
    )

def ready_pair(store, host):
    guest = invite_accept(store, "u2")
    host = mark_ready(store, host, "host")
    guest = mark_ready(store, guest, "u2")
    return host, guest

def ruleset(store, *, rounds=1, duration=30, grace=5, tie="declare_tie", sources=None, activate=True):
    cfg = {
        "round_duration_seconds": duration,
        "rounds": rounds,
        "late_event_grace_seconds": grace,
        "tie_policy": tie,
        "eligible_sources": sources or {
            "gift": {"gift_values": {"cosmic-star:v1": 100}},
            "like_batch": {"score_per_unit": 2, "max_units_per_event": 100},
            "reaction_batch": {"score_per_unit": 1, "max_units_per_event": 50},
        },
    }
    return store.create_ruleset("default", 1, "Default", cfg, "owner", activate=activate, explanation="Deterministic test rules")

def battle_ready(store, host, *, mode="1v1", rounds=1):
    host, guest = ready_pair(store, host)
    rs = ruleset(store, rounds=rounds)
    battle = store.create_battle("live1", "host", rs["id"], mode=mode)
    return battle, host, guest

def start(store, battle):
    return store.start_battle(battle["battle"]["id"], "host", command_id="start-1")


def test_capacity_invites_and_stolen_token(env):
    store, clock, host, _ = env
    invite = store.create_invitation("live1", "host", "u2", ttl_seconds=60)
    with pytest.raises(BattleDomainError) as exc:
        store.respond_invitation(invite["id"], "attacker", invite_token=invite["invite_token"], accept=True)
    assert exc.value.code == "unauthorised"
    store.respond_invitation(invite["id"], "u2", invite_token=invite["invite_token"], accept=True)
    for i in range(3, 9):
        invite_accept(store, f"u{i}")
    assert len([p for p in store.list_participants("live1") if p["join_state"] in {"lobby","connected","ready","live","reconnecting"}]) == 8
    with pytest.raises(BattleDomainError) as exc:
        store.create_invitation("live1", "host", "u9")
    assert exc.value.code == "participant_capacity_reached"


def test_invitation_decline_revoke_expiry(env):
    store, clock, host, _ = env
    i1 = store.create_invitation("live1", "host", "u2")
    out = store.respond_invitation(i1["id"], "u2", invite_token=i1["invite_token"], accept=False)
    assert out["status"] == "declined"
    i2 = store.create_invitation("live1", "host", "u3")
    assert store.revoke_invitation(i2["id"], "host")["status"] == "revoked"
    with pytest.raises(BattleDomainError):
        store.respond_invitation(i2["id"], "u3", invite_token=i2["invite_token"], accept=True)
    i3 = store.create_invitation("live1", "host", "u4", ttl_seconds=60)
    clock.advance(seconds=61)
    with pytest.raises(BattleDomainError) as exc:
        store.respond_invitation(i3["id"], "u4", invite_token=i3["invite_token"], accept=True)
    assert exc.value.code == "invite_expired"


def test_request_flow_lobby_stage_and_viewer_privacy(env):
    store, clock, host, _ = env
    req = store.request_to_join("live1", "u2")
    p = store.respond_join_request(req["id"], "host", approve=True)
    assert p["stage_state"] == "backstage" and p["join_state"] == "lobby"
    with pytest.raises(BattleDomainError) as exc:
        store.set_stage_state(p["id"], "host", "stage")
    assert exc.value.code == "participant_not_ready"
    p = mark_ready(store, p, "u2")
    store.set_stage_state(p["id"], "host", "stage")
    host = mark_ready(store, host, "host")
    rs = ruleset(store)
    b = store.create_battle("live1", "host", rs["id"], mode="1v1")
    snap = store.viewer_snapshot(b["battle"]["id"])
    visible = {x["user_id"] for x in snap["participants"]}
    assert "u2" in visible and "host" not in visible


def test_request_rate_limit_without_duplicate_pending(env):
    store, clock, host, _ = env
    con = sqlite3.connect(store.db_path)
    for i in range(2, 7):
        con.execute("INSERT INTO shared_sky_broadcasts VALUES (?,?,?)", (f"live{i}", f"h{i}", "live"))
    con.commit(); con.close()
    for i in range(1, 6):
        sid = f"live{i}"
        store.request_to_join(sid, "requester")
    with pytest.raises(BattleDomainError) as exc:
        store.request_to_join("live6", "requester")
    assert exc.value.code == "rate_limited" and exc.value.status_code == 429


def test_reconnect_preserves_slot_and_team_and_host_transfer(env):
    store, clock, host, _ = env
    battle, host, guest = battle_ready(store, host)
    bid = battle["battle"]["id"]
    before = next(x for x in battle["participants"] if x["participant_id"] == guest["id"])
    slot = guest["slot_index"]; team = before["team_id"]
    store.disconnect(guest["id"])
    restored = store.reconnect(guest["id"], "u2")
    after = next(x for x in store.battle_snapshot(bid)["participants"] if x["participant_id"] == guest["id"])
    assert restored["id"] == guest["id"] and restored["slot_index"] == slot and after["team_id"] == team
    new_host = store.transfer_host("live1", "host", guest["id"])
    assert new_host["role"] == "host"
    old = store.get_participant(host["id"])
    assert old["role"] == "cohost"


def test_start_idempotency_authoritative_timer_and_team_lock(env):
    store, clock, host, _ = env
    battle, host, guest = battle_ready(store, host)
    bid = battle["battle"]["id"]
    first = start(store, battle)
    second = store.start_battle(bid, "host", command_id="start-1")
    assert first["current_round"]["id"] == second["current_round"]["id"]
    assert first["remaining_ms"] == 30000
    clock.advance(seconds=10)
    assert store.battle_snapshot(bid)["remaining_ms"] == 20000
    team = first["teams"][0]["id"]
    with pytest.raises(BattleDomainError) as exc:
        store.assign_team(bid, guest["id"], team, "host")
    assert exc.value.code == "battle_already_active"


def test_deterministic_gift_engagement_dedup_and_reversal(env):
    store, clock, host, _ = env
    battle, host, guest = battle_ready(store, host)
    live = start(store, battle); bid = live["battle"]["id"]
    now = live["current_round"]["starts_at"]
    gift = CommittedGiftEvent("gift-event-1", "tx-1", "u2", "cosmic-star:v1", now)
    a = store.apply_committed_gift(bid, gift)
    b = store.apply_committed_gift(bid, gift)
    assert a["score_delta"] == 100 and b["deduplicated"] is True
    likes = EngagementScoreEvent("like-batch-1", "like_batch", "u2", now, count=7)
    assert store.apply_engagement(bid, likes)["score_delta"] == 14
    assert store.apply_engagement(bid, likes)["deduplicated"] is True
    assert store.reconcile(bid)["ok"] is True
    rev = ReversedGiftEvent("gift-reversal-1", "gift-event-1", now)
    out = store.reverse_gift(bid, rev)
    assert out["score_delta"] == -100
    assert store.reconcile(bid)["ok"] is True
    snap = store.battle_snapshot(bid)
    participant_scores = [v for k,v in snap["scores"].items() if k.endswith(f":participant:{guest['id']}")]
    assert participant_scores == [14]


def test_ineligible_sources_and_late_window(env):
    store, clock, host, _ = env
    battle, host, guest = battle_ready(store, host)
    live = start(store, battle); bid = live["battle"]["id"]
    t0 = live["current_round"]["starts_at"]
    with pytest.raises(BattleDomainError) as exc:
        store.apply_engagement(bid, EngagementScoreEvent("e1", "reaction_batch", "nobody", t0, count=1))
    assert exc.value.code == "source_event_ineligible"
    clock.advance(seconds=36)
    with pytest.raises(BattleDomainError) as exc:
        store.apply_committed_gift(bid, CommittedGiftEvent("late","tx-late","u2","cosmic-star:v1",t0))
    assert exc.value.code == "source_event_outside_scoring_window"


def test_round_finalise_tie_and_corrected_history(env):
    store, clock, host, _ = env
    battle, host, guest = battle_ready(store, host)
    live = start(store, battle); bid = live["battle"]["id"]
    t0 = live["current_round"]["starts_at"]
    store.apply_committed_gift(bid, CommittedGiftEvent("g1","t1","host","cosmic-star:v1",t0))
    store.apply_committed_gift(bid, CommittedGiftEvent("g2","t2","u2","cosmic-star:v1",t0))
    clock.advance(seconds=36)
    final = store.finalize_round(bid)
    assert final["battle"]["status"] == "tied"
    correction = store.reverse_gift(bid, ReversedGiftEvent("r1","g2",clock().isoformat()))
    after = store.battle_snapshot(bid)
    assert after["result"]["result_state"] == "corrected"
    assert after["battle"]["status"] == "completed"


def test_rebuild_detects_and_repairs_materialisation_drift(env):
    store, clock, host, _ = env
    battle, host, guest = battle_ready(store, host)
    live = start(store, battle); bid=live["battle"]["id"]
    t0=live["current_round"]["starts_at"]
    store.apply_committed_gift(bid, CommittedGiftEvent("g1","t1","u2","cosmic-star:v1",t0))
    con=sqlite3.connect(store.db_path)
    con.execute("UPDATE shared_sky_battle_scores SET score=999 WHERE battle_id=?",(bid,)); con.commit(); con.close()
    report=store.reconcile(bid)
    assert not report["ok"] and report["discrepancies"]
    rebuilt=store.rebuild_scores(bid)
    assert rebuilt["discrepancies"] and store.reconcile(bid)["ok"]


def test_removal_disqualification_preserves_history_and_audit(env):
    store, clock, host, _ = env
    battle, host, guest = battle_ready(store, host)
    bid=battle["battle"]["id"]
    store.remove_participant(guest["id"], "host", outcome="disqualified", reason="moderation", prevent_rejoin=True)
    snap=store.battle_snapshot(bid)
    member=next(x for x in snap["participants"] if x["participant_id"]==guest["id"])
    assert member["competitive_state"] == "disqualified"
    assert store.get_participant(guest["id"])["moderation_state"] == "banned"
    assert any(e["action"]=="participant.removed" and e["participant_id"]==guest["id"] for e in store.audit_events(bid))


def test_no_wallet_or_payout_code_path():
    from pathlib import Path
    source_path = Path(__file__).resolve().parent.parent / "aura_music_studio" / "shared_sky_battles.py"
    if not source_path.exists():
        source_path = Path(__file__).resolve().parent.parent / "aura_music_studio" / "shared_sky_battles.py"
    source = source_path.read_text(encoding="utf-8").lower()
    assert "credit_wallet" not in source
    assert ".spend(" not in source and ".grant(" not in source
    assert "payout" in source
    assert "winner-takes" not in source and "wager" not in source


def test_extra_round_tie_policy_and_paused_sweep(env):
    store, clock, host, _ = env
    host, guest = ready_pair(store, host)
    cfg={
        "round_duration_seconds": 10, "rounds": 1, "late_event_grace_seconds": 0,
        "tie_policy": "extra_round", "allow_pause": True,
        "eligible_sources": {"gift": {"fixed_score": 1}},
    }
    rs=store.create_ruleset("extra",1,"Extra",cfg,"owner",activate=True)
    b=store.create_battle("live1","host",rs["id"],mode="1v1")
    live=store.start_battle(b["battle"]["id"],"host",command_id="x")
    store.pause_battle(live["battle"]["id"],"host")
    clock.advance(seconds=20)
    assert store.finalize_due()==[]
    resumed=store.resume_battle(live["battle"]["id"],"host")
    assert resumed["remaining_ms"]==10000
    clock.advance(seconds=11)
    tied=store.finalize_round(live["battle"]["id"])
    assert tied["battle"]["status"]=="round_complete"
    assert tied["battle"]["round_count"]==2
    nxt=store.start_next_round(live["battle"]["id"],"host")
    assert nxt["battle"]["current_round_index"]==2 and nxt["battle"]["status"]=="active"


def test_sudden_death_ruleset_fails_closed(env):
    store, clock, host, _ = env
    cfg={"round_duration_seconds":10,"rounds":1,"late_event_grace_seconds":0,"tie_policy":"sudden_death","eligible_sources":{}}
    with pytest.raises(BattleDomainError) as exc:
        store.create_ruleset("sd",1,"Sudden",cfg,"owner",activate=True)
    assert exc.value.code=="ruleset_unconfigured"


def test_creator_eligibility_is_rechecked(env):
    store, clock, host, _ = env
    store.participant_eligibility=lambda uid: (uid != "blocked", "creator eligibility failed")
    with pytest.raises(BattleDomainError) as exc:
        store.create_invitation("live1","host","blocked")
    assert exc.value.code=="creator_ineligible"


def test_server_worker_finalises_due_round_without_client(env):
    from aura_music_studio.shared_sky_battle_worker import BattleWorkerSettings, SharedSkyBattleFinalizer
    store, clock, host, _ = env
    battle, host, guest = battle_ready(store, host)
    live=start(store,battle); bid=live["battle"]["id"]
    clock.advance(seconds=36)
    worker=SharedSkyBattleFinalizer(store,settings=BattleWorkerSettings(enabled=True,poll_seconds=1),worker_id="test-worker")
    assert worker.run_once()==[bid]
    assert store.battle_snapshot(bid)["battle"]["status"]=="tied"
    with store._connect() as con:
        row=con.execute("SELECT * FROM shared_sky_battle_worker_heartbeats WHERE worker_id='test-worker'").fetchone()
    assert row["status"]=="idle" and row["finalised_count"]==1


def test_concurrent_accept_never_exceeds_capacity(env):
    store, clock, host, _ = env
    for i in range(2, 8):
        invite_accept(store, f"u{i}")
    first = store.create_invitation("live1", "host", "u8")
    second = store.create_invitation("live1", "host", "u9")
    barrier = threading.Barrier(3)
    outcomes = []
    def accept(invite, user):
        barrier.wait()
        try:
            participant = store.respond_invitation(invite["id"], user, invite_token=invite["invite_token"], accept=True)
            outcomes.append(("ok", participant["id"]))
        except BattleDomainError as exc:
            outcomes.append((exc.code, None))
    a = threading.Thread(target=accept, args=(first, "u8"))
    b = threading.Thread(target=accept, args=(second, "u9"))
    a.start(); b.start(); barrier.wait(); a.join(); b.join()
    assert sorted(code for code, _ in outcomes) == ["ok", "participant_capacity_reached"]
    active = [p for p in store.list_participants("live1") if p["join_state"] in {"lobby","connected","ready","live","reconnecting"}]
    assert len(active) == 8


def test_mode_subset_stale_versions_and_controls(env):
    store, clock, host, _ = env
    guests = [invite_accept(store, f"u{i}") for i in range(2, 5)]
    host = mark_ready(store, host, "host")
    guests = [mark_ready(store, p, f"u{i}") for i, p in enumerate(guests, 2)]
    rs = ruleset(store)
    with pytest.raises(BattleDomainError) as exc:
        store.create_battle("live1", "host", rs["id"], mode="1v1")
    assert exc.value.code == "invalid_participant_set"
    selected = [host["id"], guests[0]["id"]]
    battle = store.create_battle("live1", "host", rs["id"], mode="1v1", participant_ids=selected)
    before = store.get_participant(guests[0]["id"])
    controlled = store.set_participant_controls(guests[0]["id"], "host", muted=True, camera_enabled=False, expected_version=before["version"])
    assert controlled["muted"] is True and controlled["camera_enabled"] is False
    with pytest.raises(BattleDomainError) as exc:
        store.set_stage_state(guests[0]["id"], "host", "stage", expected_version=before["version"])
    assert exc.value.code == "stale_session_version"
    team = battle["teams"][0]["id"]
    v = battle["battle"]["version"]
    updated = store.assign_team(battle["battle"]["id"], guests[0]["id"], team, "host", expected_version=v)
    assert updated["battle"]["version"] == v + 1
    with pytest.raises(BattleDomainError) as exc:
        store.assign_team(battle["battle"]["id"], guests[0]["id"], team, "host", expected_version=v)
    assert exc.value.code == "stale_session_version"


def test_source_event_identity_is_global_across_battles(env):
    store, clock, host, db = env
    host, guest = ready_pair(store, host)
    rs = ruleset(store)
    battle1 = store.create_battle("live1", "host", rs["id"], mode="1v1")
    live1 = store.start_battle(battle1["battle"]["id"], "host", command_id="start-a")
    event_time = live1["current_round"]["starts_at"]
    store.apply_committed_gift(battle1["battle"]["id"], CommittedGiftEvent("global-gift", "tx-a", "u2", "cosmic-star:v1", event_time))

    con = sqlite3.connect(db)
    con.execute("INSERT INTO shared_sky_broadcasts VALUES ('live2','h2','live')")
    con.commit(); con.close()
    h2 = store.ensure_host("live2", "h2")
    inv = store.create_invitation("live2", "h2", "v2")
    v2 = store.respond_invitation(inv["id"], "v2", invite_token=inv["invite_token"], accept=True)
    h2 = mark_ready(store, h2, "h2"); v2 = mark_ready(store, v2, "v2")
    battle2 = store.create_battle("live2", "h2", rs["id"], mode="1v1")
    live2 = store.start_battle(battle2["battle"]["id"], "h2", command_id="start-b")
    with pytest.raises(BattleDomainError) as exc:
        store.apply_committed_gift(battle2["battle"]["id"], CommittedGiftEvent("global-gift", "tx-b", "v2", "cosmic-star:v1", live2["current_round"]["starts_at"]))
    assert exc.value.code == "source_event_duplicate"


def test_realtime_cursor_is_minimal_and_monotonic(env):
    store, clock, host, _ = env
    battle, host, guest = battle_ready(store, host)
    bid = battle["battle"]["id"]
    store.start_battle(bid, "host", command_id="rt-start")
    events = store.realtime_events(bid)
    assert events and [e["cursor"] for e in events] == sorted(e["cursor"] for e in events)
    assert all(set(e) <= {"cursor", "battle_id", "event_type", "participant_id", "correlation_id", "created_at"} for e in events)
    cursor = events[-1]["cursor"]
    snap = store.battle_snapshot(bid)
    store.apply_committed_gift(bid, CommittedGiftEvent("rt-gift", "rt-tx", "u2", "cosmic-star:v1", snap["current_round"]["starts_at"]))
    later = store.realtime_events(bid, after_cursor=cursor)
    assert later and all(e["cursor"] > cursor for e in later)


def test_scheduled_battle_reschedule_convert_and_idempotent_origin(env):
    store, clock, host, _ = env
    host, guest = ready_pair(store, host)
    rs = ruleset(store)
    planned = store.schedule_battle(
        "host", rs["id"], mode="1v1", participant_user_ids=["host", "u2"],
        start_at=(clock() + timedelta(hours=2)).isoformat(), timezone_name="Europe/London", title="Friday challenge",
    )
    assert planned["status"] == "scheduled" and planned["timezone"] == "Europe/London"
    with pytest.raises(BattleDomainError) as exc:
        store.cancel_battle_plan(planned["id"], "u2")
    assert exc.value.code == "unauthorised"
    moved = store.reschedule_battle_plan(planned["id"], "host", start_at=(clock() + timedelta(hours=3)).isoformat())
    assert moved["start_at"] == (clock() + timedelta(hours=3)).isoformat()
    first = store.convert_battle_plan(planned["id"], "live1", "host")
    second = store.convert_battle_plan(planned["id"], "live1", "host")
    assert first["battle"]["id"] == second["battle"]["id"]
    assert store.battle_plan(planned["id"], "u2")["status"] == "converted"
    with store._connect() as con:
        origin = con.execute("SELECT * FROM shared_sky_battle_origins WHERE origin_type='plan' AND origin_id=?", (planned["id"],)).fetchone()
    assert origin["battle_id"] == first["battle"]["id"]


def test_challenge_requires_all_acceptances_and_creates_plan(env):
    store, clock, host, _ = env
    rs = ruleset(store)
    challenge = store.create_challenge(
        "host", rs["id"], mode="1v1", participant_user_ids=["host", "u2"],
        proposed_start_at=(clock() + timedelta(hours=1)).isoformat(), expires_seconds=600,
    )
    assert challenge["status"] == "pending" and challenge["accepted_user_ids"] == ["host"]
    accepted = store.respond_challenge(challenge["id"], "u2", accept=True)
    assert accepted["status"] == "accepted" and accepted["planned_battle_id"]
    plan = store.battle_plan(accepted["planned_battle_id"], "u2")
    assert plan["participant_user_ids"] == ["host", "u2"] and plan["status"] == "scheduled"


def test_challenge_decline_and_expiry_are_terminal(env):
    store, clock, host, _ = env
    rs = ruleset(store)
    declined = store.create_challenge("host", rs["id"], mode="1v1", participant_user_ids=["host", "u2"], proposed_start_at=clock().isoformat())
    assert store.respond_challenge(declined["id"], "u2", accept=False)["status"] == "declined"
    expired = store.create_challenge("host", rs["id"], mode="1v1", participant_user_ids=["host", "u3"], proposed_start_at=clock().isoformat(), expires_seconds=60)
    clock.advance(seconds=61)
    with pytest.raises(BattleDomainError) as exc:
        store.respond_challenge(expired["id"], "u3", accept=True)
    assert exc.value.code == "challenge_expired"


def test_completed_battle_rematch_creates_new_battle_without_score_carry(env):
    store, clock, host, _ = env
    host, guest = ready_pair(store, host)
    rs = ruleset(store, duration=10, grace=0)
    first = store.create_battle("live1", "host", rs["id"], mode="1v1")
    live = store.start_battle(first["battle"]["id"], "host", command_id="first")
    t0 = live["current_round"]["starts_at"]
    store.apply_committed_gift(first["battle"]["id"], CommittedGiftEvent("rematch-g1", "rt1", "host", "cosmic-star:v1", t0))
    clock.advance(seconds=11)
    completed = store.finalize_round(first["battle"]["id"])
    assert completed["battle"]["status"] == "completed"
    challenge = store.create_rematch_challenge(first["battle"]["id"], "host", proposed_start_at=clock().isoformat())
    accepted = store.respond_challenge(challenge["id"], "u2", accept=True)
    plan = store.battle_plan(accepted["planned_battle_id"], "host")
    second = store.convert_battle_plan(plan["id"], "live1", "host")
    assert second["battle"]["id"] != first["battle"]["id"]
    assert second["scores"] == {}


def test_best_of_series_aggregates_final_results_by_creator(env):
    store, clock, host, _ = env
    host, guest = ready_pair(store, host)
    rs = ruleset(store, duration=5, grace=0)
    series = store.create_series("host", rs["id"], mode="1v1", participant_user_ids=["host", "u2"], best_of=3, title="Best of three")
    for index in range(2):
        battle = store.create_battle("live1", "host", rs["id"], mode="1v1")
        live = store.start_battle(battle["battle"]["id"], "host", command_id=f"series-{index}")
        store.apply_committed_gift(battle["battle"]["id"], CommittedGiftEvent(f"series-g{index}", f"st{index}", "host", "cosmic-star:v1", live["current_round"]["starts_at"]))
        clock.advance(seconds=6)
        store.finalize_round(battle["battle"]["id"])
        series = store.link_series_battle(series["id"], battle["battle"]["id"], "host")
    assert series["status"] == "completed"
    assert series["winner_user_id"] == "host"
    assert series["wins"]["host"] == 2 and series["wins_required"] == 2

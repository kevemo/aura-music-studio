from __future__ import annotations

import json
from datetime import timedelta

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_security import SharedSkyVault
from aura_music_studio.shared_sky_streaming_studios import (
    BroadcastCreate,
    DestinationCreate,
    ProjectCreate,
    SharedSkyStore,
)
from aura_music_studio.shared_sky_transport_domain import SharedSkyTransportStore
from aura_music_studio.shared_sky_transport_models import iso, now


def _setup(tmp_path, *, with_destination: bool = False):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "health@example.com",
        "Health Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    base = SharedSkyStore(EspStore(accounts), SharedSkyVault("unit-test-shared-sky-secret"))
    transport = SharedSkyTransportStore(base)
    project = base.create_project(user["id"], ProjectCreate(name="Health Test"))
    source = transport.register_source(
        user["id"],
        project["id"],
        "studio_program",
        f"studio://{project['id']}",
        state="ready",
    )
    destination = None
    destination_ids = []
    if with_destination:
        destination = base.create_destination(
            user["id"],
            DestinationCreate(
                label="Health Destination",
                endpoint="rtmps://stream.example.com/live",
                credential="test-stream-key",
            ),
        )
        destination_ids.append(destination["id"])
    broadcast = base.create_broadcast(
        user["id"],
        BroadcastCreate(project_id=project["id"], destination_ids=destination_ids),
    )
    transport.configure(
        user["id"],
        broadcast["id"],
        source_id=source["id"],
        internal_playback=True,
        rendition_profile={},
        recording_enabled=False,
        ingest_session_id=None,
    )
    return user, base, transport, broadcast, destination


def test_health_heartbeat_does_not_hide_stale_starting_session(tmp_path):
    user, _base, transport, broadcast, _destination = _setup(tmp_path)
    stale_stamp = iso(now() - timedelta(minutes=10))
    with transport.connect() as con:
        con.execute(
            "UPDATE shared_sky_transport_sessions SET state='starting',updated_at=? "
            "WHERE broadcast_id=? AND user_id=?",
            (stale_stamp, broadcast["id"], user["id"]),
        )

    status = transport.report_health(
        user["id"],
        broadcast["id"],
        "transport_heartbeat",
        "ok",
        {"queue_depth": 1},
    )
    assert status["session"]["updated_at"] == stale_stamp
    assert status["session"]["health_state"] == "ok"

    recovered = transport.cleanup_stale_sessions(stale_after_seconds=60)
    assert recovered["recovered"] == 1
    assert recovered["actions"][0]["broadcast_id"] == broadcast["id"]
    assert recovered["actions"][0]["state"] == "failed"
    assert recovered["actions"][0]["reason_code"] == "stale_start_cleanup"


def test_destination_heartbeats_are_coalesced_but_latest_health_stays_visible(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SHARED_SKY_HEARTBEAT_EVENT_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("SHARED_SKY_HEALTH_STALE_AFTER_SECONDS", "5")
    user, _base, transport, broadcast, destination = _setup(
        tmp_path, with_destination=True
    )
    assert destination is not None

    transport.report_health(
        user["id"],
        broadcast["id"],
        "destination_heartbeat",
        "ok",
        {"output_bitrate_kbps": 4100, "packet_loss_percent": 0.1},
        destination_id=destination["id"],
    )
    status = transport.report_health(
        user["id"],
        broadcast["id"],
        "destination_heartbeat",
        "ok",
        {"output_bitrate_kbps": 4200, "packet_loss_percent": 0.2},
        destination_id=destination["id"],
    )

    heartbeat_events = [
        item for item in status["events"] if item["event_type"] == "destination_heartbeat"
    ]
    assert len(heartbeat_events) == 1
    run = next(
        item
        for item in status["destinations"]
        if item["destination_id"] == destination["id"]
    )
    assert run["health"]["output_bitrate_kbps"] == 4200
    assert run["health"]["packet_loss_percent"] == 0.2
    assert run["health_freshness"]["state"] == "fresh"
    assert run["health_freshness"]["event_type"] == "destination_heartbeat"
    assert run["health_freshness"]["reason_code"] == "ok"
    assert run["health_freshness"]["observed_at"]

    # A health-state change is never hidden by heartbeat coalescing.
    changed = transport.report_health(
        user["id"],
        broadcast["id"],
        "destination_heartbeat",
        "packet_loss",
        {"output_bitrate_kbps": 3000, "packet_loss_percent": 8.0},
        destination_id=destination["id"],
    )
    heartbeat_events = [
        item for item in changed["events"] if item["event_type"] == "destination_heartbeat"
    ]
    assert len(heartbeat_events) == 2

    with transport.connect() as con:
        row = con.execute(
            "SELECT health_json FROM shared_sky_destination_runs "
            "WHERE broadcast_id=? AND destination_id=?",
            (broadcast["id"], destination["id"]),
        ).fetchone()
        health = json.loads(row["health_json"])
        health["_observed_at"] = iso(now() - timedelta(seconds=30))
        con.execute(
            "UPDATE shared_sky_destination_runs SET health_json=? "
            "WHERE broadcast_id=? AND destination_id=?",
            (json.dumps(health), broadcast["id"], destination["id"]),
        )

    stale = transport.status(user["id"], broadcast["id"])
    stale_run = next(
        item
        for item in stale["destinations"]
        if item["destination_id"] == destination["id"]
    )
    assert stale_run["health_freshness"]["state"] == "stale"
    assert stale_run["health_freshness"]["age_seconds"] >= 5

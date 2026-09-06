from __future__ import annotations

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


def test_reconcile_keeps_exhausted_destination_failure_terminal(tmp_path, monkeypatch):
    from aura_music_studio import shared_sky_transport_operations as operations_module

    monkeypatch.setenv("SHARED_SKY_DESTINATION_MAX_RETRIES", "0")
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "reconnect@example.com",
        "Reconnect Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    base = SharedSkyStore(EspStore(accounts), SharedSkyVault("unit-test-shared-sky-secret"))
    transport = SharedSkyTransportStore(base)
    project = base.create_project(user["id"], ProjectCreate(name="Reconnect Test"))
    source = transport.register_source(
        user["id"],
        project["id"],
        "studio_program",
        f"studio://{project['id']}",
        state="ready",
    )
    destination = base.create_destination(
        user["id"],
        DestinationCreate(
            label="Reconnect Destination",
            endpoint="rtmps://stream.example.com/live",
            credential="test-stream-key",
        ),
    )
    broadcast = base.create_broadcast(
        user["id"],
        BroadcastCreate(project_id=project["id"], destination_ids=[destination["id"]]),
    )
    transport.configure(
        user["id"],
        broadcast["id"],
        source_id=source["id"],
        internal_playback=False,
        rendition_profile={},
        recording_enabled=False,
        ingest_session_id=None,
    )
    with transport.connect() as con:
        con.execute(
            "UPDATE shared_sky_transport_sessions SET state='live' "
            "WHERE broadcast_id=? AND user_id=?",
            (broadcast["id"], user["id"]),
        )
        con.execute(
            "UPDATE shared_sky_destination_runs SET state='live',output_id='out_dead',retry_count=0 "
            "WHERE broadcast_id=? AND destination_id=?",
            (broadcast["id"], destination["id"]),
        )

    monkeypatch.setattr(
        operations_module.relay,
        "output_state",
        lambda output_id: {"output_id": output_id, "running": False},
    )
    status = transport.reconcile(user["id"], broadcast["id"])

    assert status["session"]["state"] == "failed"
    run = next(
        item
        for item in status["destinations"]
        if item["destination_id"] == destination["id"]
    )
    assert run["state"] == "failed"
    assert run["next_retry_at"] is None
    assert run["retry_count"] == 1
    failure_events = [
        item for item in status["events"] if item["event_type"] == "destination_failure"
    ]
    assert failure_events
    assert failure_events[0]["reason_code"] == "relay_process_exited"
    assert failure_events[0]["metrics"]["retryable"] is False

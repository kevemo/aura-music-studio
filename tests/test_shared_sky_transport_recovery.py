from __future__ import annotations

from types import SimpleNamespace

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_security import SharedSkyVault
from aura_music_studio.shared_sky_streaming_studios import (
    BroadcastCreate,
    DestinationCreate,
    ProjectCreate,
    SharedSkyStore,
    SourceCreate,
)
from aura_music_studio.shared_sky_transport_domain import BroadcastState, SharedSkyTransportStore


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "recovery@example.com",
        "Recovery Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    base = SharedSkyStore(EspStore(accounts), SharedSkyVault("unit-test-shared-sky-secret"))
    transport = SharedSkyTransportStore(base)
    project = base.create_project(user["id"], ProjectCreate(name="Recovery Test"))
    base.create_source(
        user["id"],
        project["scenes"][0]["id"],
        SourceCreate(source_type="camera", name="Camera"),
    )
    source = transport.register_source(
        user["id"],
        project["id"],
        "studio_program",
        f"studio://{project['id']}",
    )
    return user, base, transport, project, source


def _broadcast(user, base, transport, project, source, destination_ids=None):
    item = base.create_broadcast(
        user["id"],
        BroadcastCreate(
            project_id=project["id"],
            destination_ids=list(destination_ids or []),
        ),
    )
    transport.configure(
        user["id"],
        item["id"],
        source_id=source["id"],
        internal_playback=True,
        rendition_profile={},
        recording_enabled=False,
    )
    return item


def test_playback_token_verifier_enforces_signature_and_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://playback.example.com/live")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "recovery-playback-signing-secret")
    user, base, transport, project, source = _setup(tmp_path)
    broadcast = _broadcast(user, base, transport, project, source)

    descriptor = transport.playback(user["id"], broadcast["id"], ttl=60)
    token = descriptor["authorization"]["token"]
    verified = transport.verify_playback_token(
        token,
        expected_broadcast_id=broadcast["id"],
        expected_user_id=user["id"],
    )
    assert verified["broadcast_id"] == broadcast["id"]
    assert verified["user_id"] == user["id"]

    with pytest.raises(ValueError):
        transport.verify_playback_token(token + "tampered")
    with pytest.raises(ValueError):
        transport.verify_playback_token(token, expected_broadcast_id="another-broadcast")
    with pytest.raises(ValueError):
        transport.verify_playback_token(token, expected_user_id="another-user")


def test_highlight_markers_are_durable_ordered_and_tenant_scoped(tmp_path):
    user, base, transport, project, source = _setup(tmp_path)
    broadcast = _broadcast(user, base, transport, project, source)

    late = transport.add_highlight_marker(
        user["id"], broadcast["id"], offset_ms=90_000, label="Second", marker_type="clip"
    )
    early = transport.add_highlight_marker(
        user["id"], broadcast["id"], offset_ms=15_000, label="First", marker_type="highlight"
    )
    recovered = SharedSkyTransportStore(base).highlight_markers(user["id"], broadcast["id"])
    assert [row["id"] for row in recovered] == [early["id"], late["id"]]
    assert [row["offset_ms"] for row in recovered] == [15_000, 90_000]
    with pytest.raises(KeyError):
        transport.highlight_markers("another-user", broadcast["id"])


def test_cleanup_stale_starting_and_stopping_sessions_is_conservative(tmp_path, monkeypatch):
    from aura_music_studio import shared_sky_transport_recovery as recovery_module

    user, base, transport, project, source = _setup(tmp_path)
    starting = _broadcast(user, base, transport, project, source)
    stopping = _broadcast(user, base, transport, project, source)
    transport._set_state(
        user["id"], starting["id"], BroadcastState.STARTING, force=True, reason="test"
    )
    transport._set_state(
        user["id"], stopping["id"], BroadcastState.STOPPING, force=True, reason="test"
    )
    with transport.connect() as con:
        con.execute(
            "UPDATE shared_sky_transport_sessions SET updated_at=? WHERE broadcast_id IN (?,?)",
            ("2000-01-01T00:00:00+00:00", starting["id"], stopping["id"]),
        )

    stopped_outputs = []
    monkeypatch.setattr(
        recovery_module.relay,
        "stop_output",
        lambda output_id: stopped_outputs.append(output_id) or True,
    )
    result = transport.cleanup_stale_sessions(stale_after_seconds=60)
    states = {
        row["broadcast_id"]: row["state"]
        for row in result["actions"]
    }
    assert states[starting["id"]] == BroadcastState.FAILED
    assert states[stopping["id"]] == BroadcastState.ENDED
    assert transport.status(user["id"], starting["id"])["session"]["state"] == BroadcastState.FAILED
    assert transport.status(user["id"], stopping["id"])["session"]["state"] == BroadcastState.ENDED


def test_stop_orders_destinations_and_stops_media_before_provider_close(tmp_path, monkeypatch):
    from aura_music_studio import shared_sky_transport_recovery as recovery_module

    user, base, transport, project, source = _setup(tmp_path)
    first = base.create_destination(
        user["id"],
        DestinationCreate(label="First", endpoint="rtmps://first.example.com/live", credential="one"),
    )
    second = base.create_destination(
        user["id"],
        DestinationCreate(label="Second", endpoint="rtmps://second.example.com/live", credential="two"),
    )
    broadcast = _broadcast(
        user,
        base,
        transport,
        project,
        source,
        destination_ids=[second["id"], first["id"]],
    )
    transport._set_state(
        user["id"], broadcast["id"], BroadcastState.LIVE, force=True, reason="test_live"
    )
    with transport.connect() as con:
        for destination in (first, second):
            con.execute(
                """UPDATE shared_sky_destination_runs
                   SET state='live',output_id=?,updated_at=datetime('now')
                   WHERE broadcast_id=? AND destination_id=?""",
                (f"out-{destination['id']}", broadcast["id"], destination["id"]),
            )

    events = []
    monkeypatch.setattr(
        recovery_module.relay,
        "stop_output",
        lambda output_id: events.append(("relay", output_id)) or True,
    )

    class FakeAdapter:
        def stop(self, *, destination, **kwargs):
            events.append(("provider", destination["id"]))

    transport.adapters["custom-rtmp"] = FakeAdapter()
    result = transport.stop(user["id"], broadcast["id"], "deterministic-stop-123")
    assert result["broadcast"]["session"]["state"] == BroadcastState.ENDED

    ordered_ids = sorted([first["id"], second["id"]])
    assert events == [
        ("relay", f"out-{ordered_ids[0]}"),
        ("provider", ordered_ids[0]),
        ("relay", f"out-{ordered_ids[1]}"),
        ("provider", ordered_ids[1]),
    ]


def test_recovery_routes_are_present_in_transport_api():
    from aura_music_studio.shared_sky_transport_api import router

    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/shared-sky/api/broadcasts/{broadcast_id}/markers" in paths
    assert "/owner/shared-sky/api/transport/cleanup-stale" in paths

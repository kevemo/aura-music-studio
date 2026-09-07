from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_destination_adapters import (
    CapabilityState,
    validate_destination_url,
)
from aura_music_studio.shared_sky_security import SharedSkyVault
from aura_music_studio.shared_sky_streaming_studios import (
    BroadcastCreate,
    DestinationCreate,
    ProjectCreate,
    SharedSkyStore,
    SourceCreate,
)
from aura_music_studio.shared_sky_transport_api import router
from aura_music_studio.shared_sky_transport_domain import (
    BroadcastState,
    OperationInProgress,
    SharedSkyTransportStore,
    TransportRateLimited,
)


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "transport@example.com",
        "Transport Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    base = SharedSkyStore(EspStore(accounts), SharedSkyVault("unit-test-shared-sky-secret"))
    return user, base, SharedSkyTransportStore(base)


def _project_source(user, base, transport, source_type="studio_program"):
    project = base.create_project(user["id"], ProjectCreate(name="Transport Test"))
    base.create_source(
        user["id"],
        project["scenes"][0]["id"],
        SourceCreate(source_type="camera", name="Camera"),
    )
    source = transport.register_source(
        user["id"],
        project["id"],
        source_type,
        f"studio://{project['id']}",
        state="ready",
    )
    return project, source


def _configure(transport, user_id, broadcast_id, source_id, **overrides):
    values = {
        "source_id": source_id,
        "internal_playback": True,
        "rendition_profile": {},
        "recording_enabled": False,
        "ingest_session_id": None,
    }
    values.update(overrides)
    return transport.configure(user_id, broadcast_id, **values)


def test_broadcast_state_machine_and_idempotent_internal_start_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://playback.example.com/live")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "test-playback-signing-secret")
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    _configure(transport, user["id"], broadcast["id"], source["id"])

    check = transport.preflight(user["id"], broadcast["id"])
    assert check["ready"] is True
    assert transport.status(user["id"], broadcast["id"])["session"]["state"] == BroadcastState.READY

    first = transport.start(user["id"], broadcast["id"], "start-one-123")
    second = transport.start(user["id"], broadcast["id"], "start-one-123")
    assert first == second
    assert first["broadcast"]["session"]["state"] == BroadcastState.LIVE
    assert first["broadcast"]["session"]["started_at"]

    stopped = transport.stop(user["id"], broadcast["id"], "stop-one-123")
    stopped_again = transport.stop(user["id"], broadcast["id"], "stop-one-123")
    assert stopped == stopped_again
    assert stopped["broadcast"]["session"]["state"] == BroadcastState.ENDED
    assert stopped["broadcast"]["session"]["ended_at"]


def test_idempotency_reservation_blocks_duplicate_in_progress_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://playback.example.com/live")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "test-playback-signing-secret")
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    _configure(transport, user["id"], broadcast["id"], source["id"])
    with transport.connect() as con:
        con.execute(
            """INSERT INTO shared_sky_transport_idempotency
               (user_id,broadcast_id,operation,idempotency_key,request_hash,response_json,created_at)
               VALUES(?,?,?,?,?,?,datetime('now'))""",
            (
                user["id"],
                broadcast["id"],
                "start",
                "same-key-123",
                hashlib.sha256(b"{}").hexdigest(),
                '{"_in_progress":true}',
            ),
        )
    with pytest.raises(OperationInProgress):
        transport.start(user["id"], broadcast["id"], "same-key-123")


def test_transport_rate_limit_is_durable_in_database(tmp_path):
    user, _base, transport = _setup(tmp_path)
    transport.rate_limit(user["id"], "test", limit=1, window_seconds=60)
    with pytest.raises(TransportRateLimited):
        SharedSkyTransportStore(transport.base).rate_limit(
            user["id"], "test", limit=1, window_seconds=60
        )


def test_destination_ssrf_validation_rejects_private_and_embedded_credentials():
    with pytest.raises(ValueError):
        validate_destination_url("rtmp://127.0.0.1/live", resolve_dns=False)
    with pytest.raises(ValueError):
        validate_destination_url("rtmps://169.254.169.254/latest/meta-data", resolve_dns=False)
    with pytest.raises(ValueError):
        validate_destination_url("rtmp://user:pass@example.com/live", resolve_dns=False)
    assert (
        validate_destination_url("rtmps://example.com/live", resolve_dns=False)
        == "rtmps://example.com/live"
    )


def test_partial_destination_failure_is_isolated_and_broadcast_is_degraded(tmp_path, monkeypatch):
    from aura_music_studio import shared_sky_transport_extensions as extension_module
    from aura_music_studio import shared_sky_transport_operations as operations_module

    monkeypatch.setenv("SHARED_SKY_INGEST_BASE_URL", "rtmps://ingest.example.com/live")
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    first = base.create_destination(
        user["id"],
        DestinationCreate(
            label="One", endpoint="rtmps://one.example.com/live", credential="key-one"
        ),
    )
    second = base.create_destination(
        user["id"],
        DestinationCreate(
            label="Two", endpoint="rtmps://two.example.com/live", credential="key-two"
        ),
    )
    broadcast = base.create_broadcast(
        user["id"],
        BroadcastCreate(
            project_id=project["id"], destination_ids=[first["id"], second["id"]]
        ),
    )
    _configure(
        transport,
        user["id"],
        broadcast["id"],
        source["id"],
        internal_playback=False,
    )

    for module in (operations_module, extension_module):
        monkeypatch.setattr(module, "validate_destination_url", lambda value, resolve_dns=True: value)
    monkeypatch.setattr(
        operations_module.relay,
        "health",
        lambda: SimpleNamespace(
            enabled=True,
            ffmpeg_available=True,
            ffmpeg_binary="ffmpeg",
            active_outputs=0,
            runtime_mode="test",
        ),
    )
    monkeypatch.setattr(
        transport.adapters["custom-rtmp"],
        "capability",
        lambda **kwargs: SimpleNamespace(
            state=CapabilityState.READY,
            reason_code="ready",
            message="ready",
        ),
    )

    def fake_start_output(**kwargs):
        if "key-two" in kwargs["output_url"]:
            raise operations_module.SharedSkyRelayError("simulated destination outage")
        return 12345

    monkeypatch.setattr(operations_module.relay, "start_output", fake_start_output)
    result = transport.start(user["id"], broadcast["id"], "partial-start-123")
    assert result["partial"] is True
    assert result["started_destinations"] == 1
    assert result["broadcast"]["session"]["state"] == BroadcastState.DEGRADED
    states = {row["destination_id"]: row["state"] for row in result["broadcast"]["destinations"]}
    assert states[first["id"]] == "live"
    assert states[second["id"]] == "reconnecting"


def test_internal_playback_can_start_when_external_destination_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://playback.example.com/live")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "test-playback-signing-secret")
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    destination = base.create_destination(
        user["id"],
        DestinationCreate(
            platform_id="youtube",
            label="YouTube pending approval",
            auth_mode="oauth",
            endpoint="",
        ),
    )
    broadcast = base.create_broadcast(
        user["id"],
        BroadcastCreate(project_id=project["id"], destination_ids=[destination["id"]]),
    )
    _configure(transport, user["id"], broadcast["id"], source["id"], internal_playback=True)

    check = transport.preflight(user["id"], broadcast["id"])
    assert check["ready"] is True
    assert check["blocking_errors"] == []
    assert any(
        warning.get("scope") == "destination"
        and warning.get("non_fatal_delivery_path_failure") is True
        for warning in check["warnings"]
    )
    started = transport.start(user["id"], broadcast["id"], "internal-only-partial-123")
    assert started["internal_playback"] is True
    assert started["partial"] is True
    assert started["broadcast"]["session"]["state"] == BroadcastState.DEGRADED


def test_destination_presets_are_durable_tenant_scoped_and_apply_to_draft(tmp_path):
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    first = base.create_destination(
        user["id"],
        DestinationCreate(label="One", endpoint="rtmps://one.example.com/live", credential="one"),
    )
    second = base.create_destination(
        user["id"],
        DestinationCreate(label="Two", endpoint="rtmps://two.example.com/live", credential="two"),
    )
    broadcast = base.create_broadcast(
        user["id"],
        BroadcastCreate(project_id=project["id"], destination_ids=[first["id"]]),
    )
    _configure(
        transport,
        user["id"],
        broadcast["id"],
        source["id"],
        internal_playback=False,
    )

    preset = transport.create_destination_preset(
        user["id"], "Launch Pair", [first["id"], second["id"], first["id"]]
    )
    assert preset["destination_ids"] == [first["id"], second["id"]]
    assert SharedSkyTransportStore(base).destination_preset(user["id"], preset["id"])["id"] == preset["id"]
    with pytest.raises(KeyError):
        transport.destination_preset("different-user", preset["id"])

    applied = transport.apply_destination_preset(user["id"], broadcast["id"], preset["id"])
    assert {row["destination_id"] for row in applied["destinations"]} == {first["id"], second["id"]}
    assert base.broadcast(user["id"], broadcast["id"])["destination_ids"] == [first["id"], second["id"]]


def test_provider_resource_ids_are_persisted_before_relay_and_reused_on_retry(tmp_path, monkeypatch):
    from aura_music_studio import shared_sky_transport_extensions as extension_module

    monkeypatch.setenv("SHARED_SKY_INGEST_BASE_URL", "rtmps://ingest.example.com/live")
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    destination = base.create_destination(
        user["id"],
        DestinationCreate(label="Provider", endpoint="rtmps://provider.example.com/live", credential="key"),
    )
    broadcast = base.create_broadcast(
        user["id"],
        BroadcastCreate(project_id=project["id"], destination_ids=[destination["id"]]),
    )
    _configure(
        transport,
        user["id"],
        broadcast["id"],
        source["id"],
        internal_playback=False,
    )
    session = transport.status(user["id"], broadcast["id"])["session"]
    seen_existing = []

    class FakeAdapter:
        def capability(self, **kwargs):
            return SimpleNamespace(
                state=CapabilityState.READY,
                reason_code="ready",
                message="ready",
            )

        def prepare(self, *, profile, **kwargs):
            seen_existing.append(dict(profile.get("_existing_run") or {}))
            return {
                "provider_broadcast_id": "provider-broadcast-1",
                "provider_stream_id": "provider-stream-1",
                "output_url": "rtmps://provider.example.com/live/key",
            }

        def stop(self, **kwargs):
            return None

    transport.adapters["custom-rtmp"] = FakeAdapter()
    monkeypatch.setattr(
        extension_module,
        "validate_destination_url",
        lambda value, resolve_dns=True: value,
    )

    def fail_start(**kwargs):
        raise extension_module.SharedSkyRelayError("relay unavailable")

    monkeypatch.setattr(extension_module.relay, "start_output", fail_start)
    ok, failure = transport._start_destination(
        user["id"], broadcast, session, destination["id"]
    )
    assert ok is False
    assert failure["reason_code"] == "destination_start_failed"
    with transport.connect() as con:
        run = dict(
            con.execute(
                "SELECT * FROM shared_sky_destination_runs WHERE broadcast_id=? AND destination_id=?",
                (broadcast["id"], destination["id"]),
            ).fetchone()
        )
    assert run["provider_external_id"] == "provider-broadcast-1"
    assert run["provider_stream_id"] == "provider-stream-1"

    monkeypatch.setattr(extension_module.relay, "start_output", lambda **kwargs: 12345)
    ok, failure = transport._start_destination(
        user["id"], broadcast, transport.status(user["id"], broadcast["id"])["session"], destination["id"]
    )
    assert ok is True
    assert failure is None
    assert seen_existing[-1] == {
        "provider_external_id": "provider-broadcast-1",
        "provider_stream_id": "provider-stream-1",
    }


def test_playback_descriptor_is_short_lived_and_authorized(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://cdn.example.com/shared-sky")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "test-playback-signing-secret")
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    _configure(transport, user["id"], broadcast["id"], source["id"])
    descriptor = transport.playback(user["id"], broadcast["id"], ttl=45)
    assert descriptor["capability_state"] == CapabilityState.READY
    assert descriptor["manifest_url"].endswith(f"/{broadcast['id']}/master.m3u8")
    assert descriptor["authorization"]["scheme"] == "Bearer"
    assert descriptor["authorization"]["token"] not in descriptor["manifest_url"]


def test_recording_handoff_masks_storage_path_and_persists_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_SKY_RECORDING_STORAGE_URI", "s3://private-bucket/shared-sky")
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    _configure(
        transport,
        user["id"],
        broadcast["id"],
        source["id"],
        recording_enabled=True,
    )
    recording = transport.request_recording(user["id"], broadcast["id"], "programme")
    assert recording["state"] == "requested"
    assert recording["storage_uri"] == "s3://private-bucket/…"


def test_transport_health_keeps_only_measurable_normalized_metrics(tmp_path):
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    _configure(
        transport,
        user["id"],
        broadcast["id"],
        source["id"],
        internal_playback=False,
    )
    status = transport.report_health(
        user["id"],
        broadcast["id"],
        "ingest_stats",
        "ok",
        {
            "input_bitrate_kbps": 4500,
            "packet_loss_percent": 0.2,
            "invented_gpu_temperature": 9000,
        },
    )
    metrics = status["events"][0]["metrics"]
    assert metrics["input_bitrate_kbps"] == 4500
    assert metrics["packet_loss_percent"] == 0.2
    assert "invented_gpu_temperature" not in metrics


def test_capacity_snapshot_reports_measured_transport_pressure(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://playback.example.com/live")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "test-playback-signing-secret")
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    _configure(transport, user["id"], broadcast["id"], source["id"])
    transport.start(user["id"], broadcast["id"], "capacity-start-123")
    transport.report_health(
        user["id"],
        broadcast["id"],
        "transport_pressure",
        "ok",
        {"queue_depth": 4, "buffer_ms": 180},
    )

    snapshot = transport.capacity_snapshot(user["id"])
    assert snapshot["active_broadcast_sessions"] == 1
    assert snapshot["max_reported_queue_depth_last_5m"] == 4
    assert snapshot["max_reported_buffer_ms_last_5m"] == 180
    assert snapshot["cpu_gpu_values_fabricated"] is False


def test_transport_state_recovers_from_same_database(tmp_path):
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport)
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    configured = _configure(
        transport,
        user["id"],
        broadcast["id"],
        source["id"],
        internal_playback=False,
        rendition_profile={"renditions": ["1080p", "720p"]},
    )
    recovered = SharedSkyTransportStore(base).status(user["id"], broadcast["id"])
    assert recovered["session"]["version"] == configured["session"]["version"]
    assert recovered["session"]["source_id"] == source["id"]
    assert recovered["session"]["rendition_set"] == ["1080p", "720p"]


def test_external_encoder_requires_verified_signed_ingest_session(tmp_path):
    user, base, transport = _setup(tmp_path)
    project, source = _project_source(user, base, transport, source_type="external_encoder")
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    _configure(
        transport,
        user["id"],
        broadcast["id"],
        source["id"],
        internal_playback=False,
        ingest_session_id="ingest_test",
    )
    check = transport.preflight(user["id"], broadcast["id"])
    codes = {item["code"] for item in check["blocking_errors"]}
    assert "signed_ingest_verifier_unavailable" in codes


def test_youtube_adapter_never_claims_live_without_verified_oauth_binding(tmp_path):
    user, base, transport = _setup(tmp_path)
    destination = base.create_destination(
        user["id"],
        DestinationCreate(platform_id="youtube", label="YouTube", auth_mode="oauth", endpoint=""),
    )
    matrix = {row["destination_id"]: row for row in transport.adapter_matrix(user["id"])}
    assert matrix[destination["id"]]["state"] in {
        CapabilityState.CREDENTIALS_MISSING,
        CapabilityState.SCOPE_INSUFFICIENT,
        CapabilityState.APPROVAL_PENDING,
    }


def test_transport_api_exposes_handoff_routes():
    paths = {getattr(route, "path", None) for route in router.routes}
    expected = {
        "/shared-sky/api/programme-sources",
        "/shared-sky/api/broadcasts/{broadcast_id}/transport",
        "/shared-sky/api/broadcasts/{broadcast_id}/transport/preflight",
        "/shared-sky/api/broadcasts/{broadcast_id}/transport/start",
        "/shared-sky/api/broadcasts/{broadcast_id}/transport/stop",
        "/shared-sky/api/broadcasts/{broadcast_id}/playback",
        "/shared-sky/api/destination-capabilities",
        "/shared-sky/api/destination-presets",
        "/shared-sky/api/broadcasts/{broadcast_id}/destination-presets/{preset_id}/apply",
        "/shared-sky/api/transport/capacity",
        "/owner/shared-sky/api/transport/capacity",
        "/shared-sky/api/destinations/validate",
    }
    assert expected.issubset(paths)

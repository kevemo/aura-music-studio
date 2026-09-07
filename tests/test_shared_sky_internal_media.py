from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_destination_adapters import CapabilityState
from aura_music_studio.shared_sky_internal_media import (
    SharedSkyInternalMediaError,
    SharedSkyInternalMediaSupervisor,
    internal_media,
)
from aura_music_studio.shared_sky_internal_media_api import router as media_router, shared_sky_media_asset
from aura_music_studio.shared_sky_security import SharedSkyVault
from aura_music_studio.shared_sky_streaming_studios import (
    BroadcastCreate,
    ProjectCreate,
    SharedSkyStore,
    SourceCreate,
)
from aura_music_studio.shared_sky_transport_domain import BroadcastState, SharedSkyTransportStore


class FakeProcess:
    _next_pid = 41000

    def __init__(self, args):
        self.args = list(args)
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1
        self._returncode = None

    def poll(self):
        return self._returncode

    def terminate(self):
        self._returncode = 0

    def kill(self):
        self._returncode = -9

    def wait(self, timeout=None):
        if self._returncode is None:
            self._returncode = 0
        return self._returncode


def _runtime_env(monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    recording_root = tmp_path / "recordings"
    monkeypatch.setenv("SHARED_SKY_INTERNAL_MEDIA_ENABLED", "1")
    monkeypatch.setenv("SHARED_SKY_INTERNAL_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("SHARED_SKY_RECORDING_LOCAL_ROOT", str(recording_root))
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://sky.example/shared-sky/media")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "test-playback-signing-secret-wave2")
    monkeypatch.setenv("SHARED_SKY_INGEST_BASE_URL", "rtmps://ingest.example.com/live")
    return media_root, recording_root


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "media@example.com",
        "Media Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    base = SharedSkyStore(EspStore(accounts), SharedSkyVault("unit-test-shared-sky-secret"))
    transport = SharedSkyTransportStore(base)
    project = base.create_project(user["id"], ProjectCreate(name="Media Runtime Test"))
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
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    transport.configure(
        user["id"],
        broadcast["id"],
        source_id=source["id"],
        internal_playback=True,
        rendition_profile={"renditions": ["720p", "480p"]},
        recording_enabled=False,
    )
    return user, base, transport, broadcast


def test_internal_playback_is_fail_closed_without_real_media_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://sky.example/shared-sky/media")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "configured-but-not-a-runtime")
    monkeypatch.setenv("SHARED_SKY_INGEST_BASE_URL", "rtmps://ingest.example.com/live")
    monkeypatch.delenv("SHARED_SKY_INTERNAL_MEDIA_ENABLED", raising=False)
    monkeypatch.delenv("SHARED_SKY_INTERNAL_MEDIA_ROOT", raising=False)
    user, _base, transport, broadcast = _setup(tmp_path)
    check = transport.preflight(user["id"], broadcast["id"])
    assert check["ready"] is False
    assert any(
        item["code"] == "internal_media_runtime_unconfigured"
        for item in check["blocking_errors"]
    )


def test_media_supervisor_builds_real_adaptive_hls_commands_and_master(tmp_path, monkeypatch):
    media_root, _recording_root = _runtime_env(monkeypatch, tmp_path)
    supervisor = SharedSkyInternalMediaSupervisor()
    monkeypatch.setattr("aura_music_studio.shared_sky_internal_media.shutil.which", lambda value: f"/usr/bin/{value}")
    created = []

    def fake_popen(args, **kwargs):
        assert kwargs.get("shell") is None
        process = FakeProcess(args)
        created.append(process)
        return process

    monkeypatch.setattr("aura_music_studio.shared_sky_internal_media.subprocess.Popen", fake_popen)
    jobs = supervisor.start_hls(
        broadcast_id="broadcast_123",
        input_url="rtmps://ingest.example.com/live/broadcast_123",
        profile={"renditions": ["1080p", "720p", "480p", "360p"]},
    )
    assert [job["rendition"] for job in jobs] == ["1080p", "720p", "480p"]
    assert len(created) == 3
    assert all("-f" in process.args and "hls" in process.args for process in created)
    master = (media_root / "broadcast_123" / "master.m3u8").read_text(encoding="utf-8")
    assert "1080p/index.m3u8" in master
    assert "720p/index.m3u8" in master
    assert "480p/index.m3u8" in master
    assert "360p/index.m3u8" not in master


def test_media_asset_path_confinement_rejects_traversal_and_unknown_types(tmp_path, monkeypatch):
    media_root, _ = _runtime_env(monkeypatch, tmp_path)
    supervisor = SharedSkyInternalMediaSupervisor()
    target = media_root / "broadcast_abc" / "720p"
    target.mkdir(parents=True)
    (target / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    assert supervisor.playback_asset("broadcast_abc", "720p/index.m3u8").is_file()
    with pytest.raises(SharedSkyInternalMediaError):
        supervisor.playback_asset("broadcast_abc", "../outside.m3u8")
    with pytest.raises(SharedSkyInternalMediaError):
        supervisor.playback_asset("broadcast_abc", "720p/secret.txt")


def test_transport_starts_internal_delivery_only_after_media_jobs_exist(tmp_path, monkeypatch):
    _runtime_env(monkeypatch, tmp_path)
    user, _base, transport, broadcast = _setup(tmp_path)
    monkeypatch.setattr(
        internal_media,
        "health",
        lambda: SimpleNamespace(
            enabled=True,
            configured=True,
            ffmpeg_available=True,
            ffprobe_available=True,
            media_root=str(tmp_path / "media"),
            recording_root_configured=True,
            active_jobs=2,
            runtime_mode="test-media",
        ),
    )
    jobs = [
        {
            "job_id": "media_hls_720",
            "broadcast_id": broadcast["id"],
            "kind": "hls",
            "rendition": "720p",
            "pid": 1001,
            "output_path": str(tmp_path / "media" / broadcast["id"] / "720p" / "index.m3u8"),
        },
        {
            "job_id": "media_hls_480",
            "broadcast_id": broadcast["id"],
            "kind": "hls",
            "rendition": "480p",
            "pid": 1002,
            "output_path": str(tmp_path / "media" / broadcast["id"] / "480p" / "index.m3u8"),
        },
    ]
    monkeypatch.setattr(internal_media, "start_hls", lambda **kwargs: jobs)
    monkeypatch.setattr(
        internal_media,
        "state",
        lambda job_id: {"running": True, "managed": True, "pid": 1001, "returncode": None},
    )
    monkeypatch.setattr(internal_media, "stop_broadcast", lambda *args, **kwargs: [])

    check = transport.preflight(user["id"], broadcast["id"])
    assert check["ready"] is True
    result = transport.start(user["id"], broadcast["id"], "wave2-internal-start")
    assert result["internal_playback"] is True
    assert result["broadcast"]["session"]["state"] == BroadcastState.LIVE
    assert len(result["broadcast"]["internal_media"]["jobs"]) == 2


def test_internal_media_start_failure_fails_when_no_other_delivery_path(tmp_path, monkeypatch):
    _runtime_env(monkeypatch, tmp_path)
    user, _base, transport, broadcast = _setup(tmp_path)
    monkeypatch.setattr(
        internal_media,
        "health",
        lambda: SimpleNamespace(
            enabled=True,
            configured=True,
            ffmpeg_available=True,
            ffprobe_available=True,
            media_root=str(tmp_path / "media"),
            recording_root_configured=True,
            active_jobs=0,
            runtime_mode="test-media",
        ),
    )
    monkeypatch.setattr(
        internal_media,
        "start_hls",
        lambda **kwargs: (_ for _ in ()).throw(SharedSkyInternalMediaError("simulated failure")),
    )
    with pytest.raises(Exception):
        transport.start(user["id"], broadcast["id"], "wave2-start-failure")
    assert transport.status(user["id"], broadcast["id"])["session"]["state"] == BroadcastState.FAILED


def test_reconcile_detects_internal_media_process_loss(tmp_path, monkeypatch):
    _runtime_env(monkeypatch, tmp_path)
    user, _base, transport, broadcast = _setup(tmp_path)
    monkeypatch.setattr(
        internal_media,
        "health",
        lambda: SimpleNamespace(
            enabled=True,
            configured=True,
            ffmpeg_available=True,
            ffprobe_available=True,
            media_root=str(tmp_path / "media"),
            recording_root_configured=True,
            active_jobs=1,
            runtime_mode="test-media",
        ),
    )
    jobs = [{
        "job_id": "media_dies",
        "broadcast_id": broadcast["id"],
        "kind": "hls",
        "rendition": "720p",
        "pid": 1003,
        "output_path": str(tmp_path / "media" / broadcast["id"] / "720p" / "index.m3u8"),
    }]
    monkeypatch.setattr(internal_media, "start_hls", lambda **kwargs: jobs)
    states = iter([
        {"running": True, "managed": True, "pid": 1003, "returncode": None},
        {"running": False, "managed": True, "pid": 1003, "returncode": 1},
    ])
    monkeypatch.setattr(internal_media, "state", lambda job_id: next(states, {"running": False, "managed": True, "pid": 1003, "returncode": 1}))
    monkeypatch.setattr(internal_media, "stop_broadcast", lambda *args, **kwargs: [])
    transport.start(user["id"], broadcast["id"], "wave2-reconcile-start")
    result = transport.reconcile(user["id"], broadcast["id"])
    assert result["session"]["state"] == BroadcastState.FAILED


def test_local_recording_root_satisfies_preflight_and_finalizes_metadata(tmp_path, monkeypatch):
    _runtime_env(monkeypatch, tmp_path)
    monkeypatch.delenv("SHARED_SKY_RECORDING_STORAGE_URI", raising=False)
    user, _base, transport, broadcast = _setup(tmp_path)
    transport.configure(
        user["id"],
        broadcast["id"],
        source_id=transport.status(user["id"], broadcast["id"])["session"]["source_id"],
        internal_playback=True,
        rendition_profile={"renditions": ["720p"]},
        recording_enabled=True,
    )
    monkeypatch.setattr(
        internal_media,
        "health",
        lambda: SimpleNamespace(
            enabled=True,
            configured=True,
            ffmpeg_available=True,
            ffprobe_available=True,
            media_root=str(tmp_path / "media"),
            recording_root_configured=True,
            active_jobs=2,
            runtime_mode="test-media",
        ),
    )
    hls_job = [{
        "job_id": "media_hls_rec",
        "broadcast_id": broadcast["id"],
        "kind": "hls",
        "rendition": "720p",
        "pid": 1004,
        "output_path": str(tmp_path / "media" / broadcast["id"] / "720p" / "index.m3u8"),
    }]
    recording_path = tmp_path / "recordings" / broadcast["id"] / "programme_test.mkv"
    recording_path.parent.mkdir(parents=True, exist_ok=True)
    recording_path.write_bytes(b"matroska-test-bytes")
    monkeypatch.setattr(internal_media, "start_hls", lambda **kwargs: hls_job)
    monkeypatch.setattr(internal_media, "state", lambda job_id: {"running": True, "managed": True, "pid": 1004, "returncode": None})
    monkeypatch.setattr(
        internal_media,
        "start_recording",
        lambda **kwargs: {
            "job_id": "media_recording",
            "broadcast_id": broadcast["id"],
            "kind": "recording:programme",
            "rendition": None,
            "pid": 1005,
            "output_path": str(recording_path),
        },
    )
    monkeypatch.setattr(
        internal_media,
        "recording_metadata",
        lambda path: {
            "exists": True,
            "size_bytes": 19,
            "checksum_sha256": "a" * 64,
            "duration_ms": 1234,
        },
    )

    check = transport.preflight(user["id"], broadcast["id"])
    assert check["ready"] is True
    assert not any(item["code"] == "recording_storage_unconfigured" for item in check["blocking_errors"])
    transport.start(user["id"], broadcast["id"], "wave2-recording-start")
    monkeypatch.setattr(
        internal_media,
        "stop_broadcast",
        lambda broadcast_id, kinds=None: ([{
            "job_id": "media_recording",
            "broadcast_id": broadcast_id,
            "kind": "recording:programme",
            "rendition": None,
            "pid": 1005,
            "output_path": str(recording_path),
            "returncode": 0,
        }] if kinds == {"recording"} else []),
    )
    stopped = transport.stop(user["id"], broadcast["id"], "wave2-recording-stop")
    recording = next(item for item in stopped["broadcast"]["recordings"] if item["kind"] == "programme")
    assert recording["state"] == "complete"
    assert recording["checksum_sha256"] == "a" * 64
    assert recording["size_bytes"] == 19
    assert recording["duration_ms"] == 1234
    assert "programme_test.mkv" not in recording["storage_uri"]


def test_signed_media_origin_requires_bound_bearer_and_serves_hls(tmp_path, monkeypatch):
    media_root, _ = _runtime_env(monkeypatch, tmp_path)
    target = media_root / "broadcast_route" / "master.m3u8"
    target.parent.mkdir(parents=True)
    target.write_text("#EXTM3U\n", encoding="utf-8")
    from aura_music_studio.shared_sky_transport_domain import transport
    body_user = "route-user"
    expiry_descriptor = transport.verify_playback_token
    # Generate through the same transport signing contract without requiring a DB row.
    import hashlib
    import hmac
    import secrets
    from datetime import timedelta
    from aura_music_studio.shared_sky_transport_models import now
    expiry = int((now() + timedelta(seconds=60)).timestamp())
    nonce = secrets.token_urlsafe(12)
    body = f"broadcast_route.{body_user}.{expiry}.{nonce}"
    secret = "test-playback-signing-secret-wave2"
    token = body + "." + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    response = shared_sky_media_asset(
        "broadcast_route", "master.m3u8", authorization=f"Bearer {token}"
    )
    assert Path(response.path).resolve() == target.resolve()
    with pytest.raises(Exception):
        shared_sky_media_asset("another_broadcast", "master.m3u8", authorization=f"Bearer {token}")
    with pytest.raises(Exception):
        shared_sky_media_asset("broadcast_route", "../master.m3u8", authorization=f"Bearer {token}")


def test_media_routes_include_signed_origin_and_owner_status():
    paths = {getattr(route, "path", None) for route in media_router.routes}
    assert "/shared-sky/media/{broadcast_id}/{asset_path:path}" in paths
    assert "/owner/shared-sky/api/internal-media/status" in paths

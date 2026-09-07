from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_internal_media import MediaProcess, internal_media
from aura_music_studio.shared_sky_security import SharedSkyVault
from aura_music_studio.shared_sky_streaming_studios import (
    BroadcastCreate,
    ProjectCreate,
    SharedSkyStore,
    SourceCreate,
)
from aura_music_studio.shared_sky_transport_domain import BroadcastState, SharedSkyTransportStore


class _FakeProcess:
    def __init__(self, pid: int, returncode=None):
        self.pid = pid
        self._returncode = returncode

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


def _setup(tmp_path, monkeypatch, *, recording_enabled: bool = False):
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://sky.example/shared-sky/media")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "startup-readiness-secret")
    monkeypatch.setenv("SHARED_SKY_INGEST_BASE_URL", "rtmps://ingest.example.com/live")
    monkeypatch.setenv("SHARED_SKY_INTERNAL_MEDIA_ENABLED", "1")
    monkeypatch.setenv("SHARED_SKY_INTERNAL_MEDIA_ROOT", str(tmp_path / "media"))
    if recording_enabled:
        monkeypatch.setenv("SHARED_SKY_RECORDING_LOCAL_ROOT", str(tmp_path / "recordings"))

    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "startup@example.com",
        "Startup Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    base = SharedSkyStore(EspStore(accounts), SharedSkyVault("unit-test-shared-sky-secret"))
    transport = SharedSkyTransportStore(base)
    project = base.create_project(user["id"], ProjectCreate(name="Startup Readiness"))
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
        state="ready",
    )
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )
    transport.configure(
        user["id"],
        broadcast["id"],
        source_id=source["id"],
        internal_playback=True,
        rendition_profile={"renditions": ["720p"]},
        recording_enabled=recording_enabled,
        ingest_session_id=None,
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
            recording_root_configured=recording_enabled,
            active_jobs=0,
            runtime_mode="test-media",
        ),
    )
    return user, transport, broadcast


def _install_managed_job(monkeypatch, tmp_path, broadcast_id: str, *, ready: bool, returncode=None):
    output = tmp_path / "media" / broadcast_id / "720p" / "index.m3u8"
    if ready:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("#EXTM3U\n#EXT-X-VERSION:3\n", encoding="utf-8")
    job_id = "managed_hls_startup_test"
    process = _FakeProcess(51001, returncode=returncode)
    item = MediaProcess(
        job_id=job_id,
        broadcast_id=broadcast_id,
        kind="hls",
        rendition="720p",
        process=process,
        output_path=output,
    )

    def start_hls(**kwargs):
        with internal_media._lock:
            internal_media._jobs[job_id] = item
        return [{
            "job_id": job_id,
            "broadcast_id": broadcast_id,
            "kind": "hls",
            "rendition": "720p",
            "pid": process.pid,
            "output_path": str(output),
        }]

    monkeypatch.setattr(internal_media, "start_hls", start_hls)
    return job_id, output


def _install_external_worker_ack(monkeypatch, tmp_path, broadcast_id: str):
    output = tmp_path / "worker" / broadcast_id / "720p" / "index.m3u8"
    monkeypatch.setattr(
        internal_media,
        "start_hls",
        lambda **kwargs: [{
            "job_id": "external_worker_hls",
            "broadcast_id": broadcast_id,
            "kind": "hls",
            "rendition": "720p",
            "pid": 52001,
            "output_path": str(output),
        }],
    )


def test_internal_live_requires_nonempty_playlist_from_managed_media_process(tmp_path, monkeypatch):
    user, transport, broadcast = _setup(tmp_path, monkeypatch)
    job_id, output = _install_managed_job(
        monkeypatch, tmp_path, broadcast["id"], ready=True, returncode=None
    )
    result = transport.start(user["id"], broadcast["id"], "startup-ready-key")
    assert output.stat().st_size > 0
    assert result["internal_playback"] is True
    assert result["broadcast"]["session"]["state"] == BroadcastState.LIVE
    assert any(
        event["event_type"] == "internal_playback_ready"
        for event in result["broadcast"]["events"]
    )
    with internal_media._lock:
        internal_media._jobs.pop(job_id, None)


def test_managed_media_process_exit_before_playlist_fails_broadcast(tmp_path, monkeypatch):
    user, transport, broadcast = _setup(tmp_path, monkeypatch)
    job_id, output = _install_managed_job(
        monkeypatch, tmp_path, broadcast["id"], ready=False, returncode=1
    )
    assert not output.exists()
    with pytest.raises(Exception):
        transport.start(user["id"], broadcast["id"], "startup-failed-key")
    status = transport.status(user["id"], broadcast["id"])
    assert status["session"]["state"] == BroadcastState.FAILED
    with transport.connect() as con:
        row = con.execute(
            "SELECT state,reason_code FROM shared_sky_internal_media_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    assert row["state"] == "failed"
    assert row["reason_code"] == "internal_playback_process_exited_before_ready"
    with internal_media._lock:
        internal_media._jobs.pop(job_id, None)


def test_media_filesystem_oserror_is_normalized_and_never_leaves_starting_state(tmp_path, monkeypatch):
    user, transport, broadcast = _setup(tmp_path, monkeypatch)

    def fail_hls(**kwargs):
        raise OSError("simulated media root failure")

    monkeypatch.setattr(internal_media, "start_hls", fail_hls)
    with pytest.raises(Exception):
        transport.start(user["id"], broadcast["id"], "startup-oserror-key")
    assert transport.status(user["id"], broadcast["id"])["session"]["state"] == BroadcastState.FAILED


def test_recording_process_oserror_degrades_live_instead_of_escaping_lifecycle(tmp_path, monkeypatch):
    user, transport, broadcast = _setup(tmp_path, monkeypatch, recording_enabled=True)
    _install_external_worker_ack(monkeypatch, tmp_path, broadcast["id"])

    def fail_recording(**kwargs):
        raise OSError("simulated recorder spawn failure")

    monkeypatch.setattr(internal_media, "start_recording", fail_recording)
    result = transport.start(user["id"], broadcast["id"], "recording-oserror-key")
    assert result["internal_playback"] is True
    assert result["partial"] is True
    assert result["broadcast"]["session"]["state"] == BroadcastState.DEGRADED
    assert {item["reason_code"] for item in result["failures"]} == {"recording_start_failed"}

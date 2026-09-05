from __future__ import annotations

from types import SimpleNamespace

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_internal_media import internal_media
from aura_music_studio.shared_sky_security import SharedSkyVault
from aura_music_studio.shared_sky_streaming_studios import (
    BroadcastCreate,
    ProjectCreate,
    SharedSkyStore,
)
from aura_music_studio.shared_sky_transport_domain import SharedSkyTransportStore


def test_member_transport_status_redacts_internal_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_BASE_URL", "https://sky.example/shared-sky/media")
    monkeypatch.setenv("SHARED_SKY_PLAYBACK_SIGNING_SECRET", "privacy-test-signing-secret")
    monkeypatch.setenv("SHARED_SKY_INTERNAL_MEDIA_ENABLED", "1")
    monkeypatch.setenv("SHARED_SKY_INTERNAL_MEDIA_ROOT", str(tmp_path / "private-media-root"))
    monkeypatch.setenv("SHARED_SKY_INGEST_BASE_URL", "rtmps://ingest.example.com/live")

    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "privacy@example.com",
        "Privacy Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    base = SharedSkyStore(EspStore(accounts), SharedSkyVault("unit-test-shared-sky-secret"))
    transport = SharedSkyTransportStore(base)
    project = base.create_project(user["id"], ProjectCreate(name="Privacy Test"))
    broadcast = base.create_broadcast(
        user["id"], BroadcastCreate(project_id=project["id"], destination_ids=[])
    )

    monkeypatch.setattr(
        internal_media,
        "health",
        lambda: SimpleNamespace(
            enabled=True,
            configured=True,
            ffmpeg_available=True,
            ffprobe_available=True,
            media_root=str(tmp_path / "private-media-root"),
            recording_root_configured=True,
            active_jobs=3,
            runtime_mode="self-host-process",
        ),
    )

    status = transport.status(user["id"], broadcast["id"])
    health = status["internal_media"]["health"]
    assert "media_root" not in health
    assert health["media_root_exposed"] is False
    assert health["active_jobs"] == 3
    assert str(tmp_path) not in repr(status)

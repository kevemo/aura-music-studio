from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_security import SharedSkyVault, SharedSkyVaultError
from aura_music_studio.shared_sky_streaming_studios import (
    AUDIO_CATALOG,
    EFFECT_CATALOG,
    MENU,
    PLATFORM_REGISTRY,
    SOURCE_CATALOG,
    TRANSITION_CATALOG,
    BroadcastCreate,
    DestinationCreate,
    ProjectCreate,
    SceneCreate,
    SharedSkyStore,
    SourceCreate,
    router,
)


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup("sky@example.com", "Shared Sky Creator", "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    esp = EspStore(accounts)
    return user, SharedSkyStore(esp, SharedSkyVault("unit-test-shared-sky-secret"))


def test_shared_sky_project_scene_and_source_are_tenant_scoped(tmp_path):
    user, store = _setup(tmp_path)
    project = store.create_project(user["id"], ProjectCreate(name="Friday Show"))
    assert project["name"] == "Friday Show"
    assert len(project["scenes"]) == 1

    scene = store.create_scene(user["id"], project["id"], SceneCreate(name="Interview"))
    source = store.create_source(
        user["id"],
        scene["id"],
        SourceCreate(source_type="camera", name="Host Camera", config={"effects": ["colour_correction"]}),
    )
    assert source["source_type"] == "camera"
    assert source["config"]["effects"] == ["colour_correction"]

    with pytest.raises(KeyError):
        store.project("different-user", project["id"])
    with pytest.raises(KeyError):
        store.scene("different-user", scene["id"])
    with pytest.raises(KeyError):
        store.source("different-user", source["id"])


def test_destination_credentials_are_encrypted_and_never_returned(tmp_path):
    user, store = _setup(tmp_path)
    destination = store.create_destination(
        user["id"],
        DestinationCreate(
            platform_id="custom-rtmp",
            label="Main channel",
            auth_mode="custom_rtmp",
            endpoint="rtmps://example.invalid/live",
            credential="super-secret-stream-key",
        ),
    )
    assert destination["credential_stored"] is True
    assert "credential_ciphertext" not in destination
    assert "super-secret-stream-key" not in repr(destination)

    raw = store._owned("shared_sky_destinations", destination["id"], user["id"])
    assert raw["credential_ciphertext"]
    assert raw["credential_ciphertext"] != "super-secret-stream-key"
    assert store.vault.decrypt(raw["credential_ciphertext"]) == "super-secret-stream-key"


def test_vault_fails_closed_when_not_configured():
    vault = SharedSkyVault("")
    assert vault.configured is False
    with pytest.raises(SharedSkyVaultError):
        vault.encrypt("do-not-store-in-plaintext")


def test_preflight_is_fail_closed_without_relay_and_ingest(tmp_path, monkeypatch):
    monkeypatch.delenv("SHARED_SKY_INGEST_BASE_URL", raising=False)
    user, store = _setup(tmp_path)
    project = store.create_project(user["id"], ProjectCreate(name="Fail Closed"))
    scene_id = project["scenes"][0]["id"]
    store.create_source(user["id"], scene_id, SourceCreate(source_type="camera", name="Camera"))
    destination = store.create_destination(
        user["id"],
        DestinationCreate(
            platform_id="custom-rtmp",
            label="Custom",
            endpoint="rtmps://example.invalid/live",
            credential="key",
        ),
    )
    broadcast = store.create_broadcast(
        user["id"],
        BroadcastCreate(project_id=project["id"], destination_ids=[destination["id"]]),
    )
    check = store.preflight(user["id"], broadcast["id"])
    assert check["ready"] is False
    assert any("SHARED_SKY_INGEST_BASE_URL" in reason for reason in check["reasons"])


def test_platform_and_creative_catalogs_are_deep():
    ids = {row["id"] for row in PLATFORM_REGISTRY}
    assert {"youtube", "facebook", "twitch", "tiktok", "instagram", "custom-rtmp"}.issubset(ids)
    assert len(SOURCE_CATALOG) >= 30
    assert len(EFFECT_CATALOG) >= 35
    assert len(TRANSITION_CATALOG) >= 15
    assert len(AUDIO_CATALOG) >= 20
    assert "Go Live" in MENU
    assert "Guests & Green Room" in MENU
    assert "Unified Chat" in MENU
    assert "Plugins & Integrations" in MENU


def test_shared_sky_routes_cover_member_studio_and_owner_control():
    paths = {getattr(route, "path", None) for route in router.routes}
    expected = {
        "/shared-sky",
        "/shared-sky/api/catalog",
        "/shared-sky/api/state",
        "/shared-sky/api/projects",
        "/shared-sky/api/projects/{project_id}/scenes",
        "/shared-sky/api/scenes/{scene_id}/sources",
        "/shared-sky/api/destinations",
        "/shared-sky/api/broadcasts",
        "/shared-sky/api/broadcasts/{broadcast_id}/preflight",
        "/shared-sky/api/broadcasts/{broadcast_id}/start",
        "/shared-sky/api/broadcasts/{broadcast_id}/stop",
        "/shared-sky/api/broadcasts/{broadcast_id}/health",
        "/shared-sky/api/schedules",
        "/owner/shared-sky",
        "/owner/shared-sky/api/status",
        "/owner/shared-sky/api/emergency-stop",
    }
    assert expected.issubset(paths)


def test_platform_registry_does_not_pretend_external_approvals_are_live():
    for platform in PLATFORM_REGISTRY:
        if platform["id"] in {"youtube", "facebook", "twitch", "tiktok", "instagram", "linkedin", "x"}:
            assert platform["implementation"] != "framework_ready"

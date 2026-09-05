from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from aura_music_studio.creation_live import (
    LIVE_UI_SCRIPT,
    CreationLiveSourceDescriptor,
    CreationLiveStore,
    RightsPreflight,
    SourceCapabilities,
    _adapter_id,
    _contains_private_metadata,
    _rights_for,
    router,
)
from aura_music_studio.models import ProjectManifest
from aura_music_studio.rights import RightsLedger, VoiceProfile
from aura_music_studio.route_integrity import deduplicate_http_routes


def _descriptor(source_adapter_id: str = "cls_test", *, studio_type: str = "music", source_type: str = "clean_music_output"):
    return CreationLiveSourceDescriptor(
        source_adapter_id=source_adapter_id,
        studio_type=studio_type,
        project_id="song-one",
        workspace_id="creator-a",
        creator_id="creator-a",
        source_type=source_type,
        safe_display_name="Clean output",
        media_kind="audio" if studio_type == "music" else "audiovisual",
        capabilities=SourceCapabilities(audio=True, video=studio_type != "music"),
        privacy_classification="project_safe_output",
        inclusion_manifest=["approved project output"],
        rights=RightsPreflight(state="ready"),
    )


def test_source_descriptor_is_safe_and_does_not_serialize_server_ref():
    row = _descriptor().model_dump(mode="json")
    assert "server_ref" not in row
    assert "filesystem_path" not in json.dumps(row)
    assert row["privacy_classification"] == "project_safe_output"


def test_source_schema_rejects_cross_studio_source_type():
    with pytest.raises(ValidationError):
        _descriptor(studio_type="image_visual", source_type="selected_stem")


def test_private_metadata_detector_recurses_without_exposing_value():
    findings = _contains_private_metadata({"safe": {"provider": {"access_token": "do-not-leak"}}})
    assert findings == ["safe.provider.access_token"]
    assert "do-not-leak" not in repr(findings)


def test_source_identity_is_stable_and_tenant_scoped():
    first = _adapter_id("u1", "p1", "music", "file:output/master.wav")
    assert first == _adapter_id("u1", "p1", "music", "file:output/master.wav")
    assert first != _adapter_id("u2", "p1", "music", "file:output/master.wav")
    assert first != _adapter_id("u1", "p2", "music", "file:output/master.wav")


def test_store_attach_state_uses_optimistic_version_and_editor_ownership(tmp_path):
    store = CreationLiveStore(tmp_path / "live.sqlite3")
    descriptor = _descriptor()
    stored = store.upsert_discovered("creator-a", descriptor, "master", "output/master.wav")
    assert stored["version"] == 1
    attached = store.mutate(
        "creator-a",
        descriptor.source_adapter_id,
        expected_version=1,
        editor_instance_id="editor-one",
        source_status="registered",
        active_editor_instance_id="editor-one",
    )
    assert attached["version"] == 2
    with pytest.raises(RuntimeError, match="stale_source_version"):
        store.mutate("creator-a", descriptor.source_adapter_id, expected_version=1, editor_instance_id="editor-one")
    with pytest.raises(RuntimeError, match="source_controlled_by_another_editor"):
        store.mutate("creator-a", descriptor.source_adapter_id, expected_version=2, editor_instance_id="editor-two")


def test_attach_detach_idempotency_replays_only_same_request(tmp_path):
    store = CreationLiveStore(tmp_path / "live.sqlite3")
    descriptor = _descriptor()
    store.upsert_discovered("creator-a", descriptor, "master", "output/master.wav")
    calls = []

    def execute():
        calls.append(1)
        return {"ok": True}

    request = {"source": descriptor.source_adapter_id, "expected_version": 1}
    assert store.idempotent("creator-a", "attach", "operation-123", descriptor.source_adapter_id, request, execute) == {"ok": True}
    assert store.idempotent("creator-a", "attach", "operation-123", descriptor.source_adapter_id, request, execute) == {"ok": True}
    assert len(calls) == 1
    with pytest.raises(RuntimeError, match="different_request"):
        store.idempotent("creator-a", "attach", "operation-123", descriptor.source_adapter_id, {"source": "other"}, execute)


def test_hidden_restricted_and_secret_metadata_fail_closed(tmp_path):
    hidden = _rights_for(tmp_path, "image_visual", metadata={"hidden": True, "rights_record_id": "rr"})
    assert hidden.state == "blocked"
    assert "private_asset_not_eligible" in hidden.codes
    secret = _rights_for(tmp_path, "video_cinema", metadata={"provider_payload": {"access_token": "secret"}})
    assert secret.state == "blocked"
    assert "private_metadata_detected" in secret.codes


def test_real_person_likeness_requires_live_permission(tmp_path):
    result = _rights_for(tmp_path, "image_visual", metadata={"real_person_likeness": True, "likeness_consent": False})
    assert result.state == "blocked"
    assert "likeness_or_voice_not_authorised_for_live" in result.codes


def test_voice_model_project_permission_does_not_imply_live_permission(tmp_path):
    ledger = RightsLedger(tmp_path / ".aura_rights")
    profile = ledger.save_voice(
        VoiceProfile(
            name="Authorised singer",
            owner_label="Creator",
            consent_confirmed=True,
            consent_statement="I consent to this voice profile for the listed authorised uses.",
            allowed_uses=["singing", "voice_conversion"],
        )
    )
    result = _rights_for(tmp_path, "music", metadata={"voice_profile_id": profile.id, "rights_record_id": "rr"})
    assert result.state == "blocked"
    assert "likeness_or_voice_not_authorised_for_live" in result.codes


def test_voice_model_can_be_explicitly_authorised_for_live(tmp_path):
    ledger = RightsLedger(tmp_path / ".aura_rights")
    profile = ledger.save_voice(
        VoiceProfile(
            name="Live singer",
            owner_label="Creator",
            consent_confirmed=True,
            consent_statement="I consent to this voice profile being used in authorised live streams.",
            allowed_uses=["singing", "live_streaming"],
        )
    )
    result = _rights_for(tmp_path, "music", metadata={"voice_profile_id": profile.id, "rights_record_id": "rr"})
    assert result.state == "ready"


def test_music_cover_without_broadcast_rights_is_blocked(tmp_path):
    legacy = ProjectManifest.model_construct(project_name="cover", title="Cover", mode="cover", rights_confirmed=False)
    result = _rights_for(tmp_path, "music", legacy_music=legacy)
    assert result.state == "blocked"
    assert "project_rights_blocked" in result.codes


def test_full_workspace_is_never_silent_default(tmp_path):
    result = _rights_for(tmp_path, "video_cinema", advanced_workspace=True)
    assert result.state == "warning"
    assert result.requires_confirmation is True
    assert "advanced_workspace_privacy_warning" in result.codes
    assert "getDisplayMedia" in LIVE_UI_SCRIPT
    assert "full workspace is advanced and never the default" in LIVE_UI_SCRIPT.lower()


def test_ui_never_equates_session_live_with_project_source_on_air():
    assert "NOT CONFIRMED ON AIR" in LIVE_UI_SCRIPT
    assert "Nothing is ON AIR until Shared Sky confirms" in LIVE_UI_SCRIPT
    assert "This is still not attached or ON AIR" in LIVE_UI_SCRIPT


def test_ui_cleans_browser_capture_tracks_on_close():
    assert "getTracks().forEach(t=>t.stop())" in LIVE_UI_SCRIPT


def test_chat7_has_no_wallet_or_battle_score_engine():
    source = LIVE_UI_SCRIPT.lower()
    assert "debit" not in source
    assert "wallet" not in source
    assert "battle score" not in source


def test_router_exposes_source_lifecycle_privacy_and_return_contracts():
    paths = {getattr(route, "path", None) for route in router.routes}
    expected = {
        "/creation-live/capabilities",
        "/creation-live/projects/{project_name}/sources",
        "/creation-live/projects/{project_name}/sources/{source_adapter_id}",
        "/creation-live/projects/{project_name}/sources/{source_adapter_id}/media",
        "/creation-live/projects/{project_name}/sources/{source_adapter_id}/attach",
        "/creation-live/projects/{project_name}/sources/{source_adapter_id}/transition",
        "/creation-live/projects/{project_name}/sources/{source_adapter_id}/emergency-hide",
        "/creation-live/projects/{project_name}/sources/{source_adapter_id}/detach",
        "/creation-live/projects/{project_name}/markers",
        "/creation-live/projects/{project_name}/returns",
        "/creation-live/projects/{project_name}/community",
        "/creation-live/projects/{project_name}/aura-assistance",
        "/creation-live/ui.js",
    }
    assert expected.issubset(paths)


def test_canonical_route_composition_installs_chat7_once():
    app = FastAPI()
    deduplicate_http_routes(app)
    deduplicate_http_routes(app)
    matches = [route for route in app.router.routes if getattr(route, "path", None) == "/creation-live/capabilities"]
    assert len(matches) == 1
    assert app.state.creation_live_installed is True

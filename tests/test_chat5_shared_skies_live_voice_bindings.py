from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from aura_music_studio import tenant_storage
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.rights import RightsLedger, VoiceProfile
from aura_music_studio.shared_sky_control_room import StudioConflict, StudioInvariantError
import aura_music_studio.shared_skies_live_voice_bindings as bindings
from aura_music_studio.shared_skies_live_voice_bindings import (
    LiveVoiceBindingDelete,
    LiveVoiceBindingService,
    LiveVoiceBindingStore,
    LiveVoiceBindingUpsert,
    install_shared_skies_live_voice_bindings,
)


def _store(tmp_path) -> LiveVoiceBindingStore:
    db_path = tmp_path / "bindings.db"
    with sqlite3.connect(db_path) as con:
        con.executescript(
            """
            CREATE TABLE shared_sky_studio_sessions(id TEXT PRIMARY KEY);
            CREATE TABLE shared_sky_sources(id TEXT PRIMARY KEY);
            INSERT INTO shared_sky_studio_sessions(id) VALUES('session-one');
            INSERT INTO shared_sky_sources(id) VALUES('mic-one');
            INSERT INTO shared_sky_sources(id) VALUES('image-one');
            """
        )
    return LiveVoiceBindingStore(str(db_path))


def _ledger(tmp_path, monkeypatch, user_id: str, project_name: str) -> RightsLedger:
    monkeypatch.setattr(tenant_storage, "ROOT", tmp_path / "tenant-root")
    token = set_current_user_id(user_id)
    try:
        project = tenant_storage.project_path(project_name, must_exist=False)
        project.mkdir(parents=True, exist_ok=True)
        return RightsLedger(project / ".aura_rights")
    finally:
        reset_current_user_id(token)


def _profile(ledger: RightsLedger, user_id: str, *, uses: list[str]) -> VoiceProfile:
    token = set_current_user_id(user_id)
    try:
        return ledger.save_voice(
            VoiceProfile(
                name="Authorised Voice",
                owner_label="Voice owner",
                reference_files=["/private/reference.wav"],
                consent_confirmed=True,
                consent_statement="I explicitly authorise this voice profile for the declared uses.",
                verification_state="attested",
                allowed_uses=uses,
            )
        )
    finally:
        reset_current_user_id(token)


def _install_fake_studio(monkeypatch, *, source_type: str = "microphone") -> dict:
    source = {
        "id": "mic-one" if source_type == "microphone" else "image-one",
        "project_id": "studio-project",
        "source_type": source_type,
        "name": "Input",
        "config": {"audio": {"gain": 1.0}, "device_hint": "browser-private"},
    }
    fake_repo = SimpleNamespace(
        get_session=lambda user_id, session_id: {
            "id": session_id,
            "user_id": user_id,
            "project_id": "studio-project",
        }
    )
    fake_graph = SimpleNamespace(source=lambda user_id, source_id: dict(source))
    monkeypatch.setattr(bindings, "studio_repo", fake_repo)
    monkeypatch.setattr(bindings, "studio", SimpleNamespace(graph=fake_graph))
    return source


def test_binding_persists_only_stable_refs_and_never_activates_processor(tmp_path, monkeypatch):
    user_id = "creator-one"
    ledger = _ledger(tmp_path, monkeypatch, user_id, "voice-house")
    profile = _profile(ledger, user_id, uses=["speech", "voice_conversion"])
    source = _install_fake_studio(monkeypatch)
    service = LiveVoiceBindingService(_store(tmp_path))

    token = set_current_user_id(user_id)
    try:
        result = service.bind(
            user_id,
            "session-one",
            "mic-one",
            LiveVoiceBindingUpsert(
                chat2_project_name="voice-house",
                profile_id=profile.id,
                purpose="speech",
            ),
        )
    finally:
        reset_current_user_id(token)

    raw = service.store.binding(user_id, "session-one", "mic-one")
    assert set(raw) == {
        "id",
        "user_id",
        "session_id",
        "source_id",
        "chat2_project_name",
        "profile_id",
        "purpose",
        "version",
        "created_at",
        "updated_at",
    }
    assert "/private/reference.wav" not in repr(raw)
    assert "browser-private" not in repr(raw)
    assert result["binding_state"] == "reference_bound"
    assert result["currently_authorised"] is True
    assert result["processor_runtime_attached"] is False
    assert result["processor_activation_allowed"] is False
    assert result["real_time_processing_proven"] is False
    assert result["final_execution_reauthorisation_required"] is True
    assert result["entitlement_evaluated_by_chat5"] is False
    assert source["config"]["device_hint"] == "browser-private"


def test_existing_binding_fails_closed_after_profile_revocation(tmp_path, monkeypatch):
    user_id = "creator-one"
    ledger = _ledger(tmp_path, monkeypatch, user_id, "voice-house")
    profile = _profile(ledger, user_id, uses=["speech"])
    _install_fake_studio(monkeypatch)
    service = LiveVoiceBindingService(_store(tmp_path))

    token = set_current_user_id(user_id)
    try:
        service.bind(
            user_id,
            "session-one",
            "mic-one",
            LiveVoiceBindingUpsert(chat2_project_name="voice-house", profile_id=profile.id, purpose="speech"),
        )
        ledger.revoke_voice(profile.id, "Consent withdrawn")
        rows = service.list_bindings(user_id, "session-one")
    finally:
        reset_current_user_id(token)

    assert len(rows) == 1
    assert rows[0]["currently_authorised"] is False
    assert rows[0]["authorisation_state"] == "unavailable_or_revoked"
    assert rows[0]["profile"] is None
    assert rows[0]["processor_activation_allowed"] is False


def test_binding_rejects_wrong_purpose_and_non_audio_source(tmp_path, monkeypatch):
    user_id = "creator-one"
    ledger = _ledger(tmp_path, monkeypatch, user_id, "voice-house")
    profile = _profile(ledger, user_id, uses=["singing"])
    service = LiveVoiceBindingService(_store(tmp_path))

    _install_fake_studio(monkeypatch)
    token = set_current_user_id(user_id)
    try:
        with pytest.raises(PermissionError):
            service.bind(
                user_id,
                "session-one",
                "mic-one",
                LiveVoiceBindingUpsert(chat2_project_name="voice-house", profile_id=profile.id, purpose="speech"),
            )
    finally:
        reset_current_user_id(token)

    _install_fake_studio(monkeypatch, source_type="image")
    token = set_current_user_id(user_id)
    try:
        with pytest.raises(StudioInvariantError):
            service.bind(
                user_id,
                "session-one",
                "image-one",
                LiveVoiceBindingUpsert(chat2_project_name="voice-house", profile_id=profile.id, purpose="singing"),
            )
    finally:
        reset_current_user_id(token)


def test_binding_requires_matching_tenant_context(tmp_path, monkeypatch):
    user_id = "creator-one"
    ledger = _ledger(tmp_path, monkeypatch, user_id, "voice-house")
    profile = _profile(ledger, user_id, uses=["speech"])
    _install_fake_studio(monkeypatch)
    service = LiveVoiceBindingService(_store(tmp_path))

    token = set_current_user_id("other-user")
    try:
        with pytest.raises(PermissionError):
            service.bind(
                user_id,
                "session-one",
                "mic-one",
                LiveVoiceBindingUpsert(chat2_project_name="voice-house", profile_id=profile.id, purpose="speech"),
            )
    finally:
        reset_current_user_id(token)


def test_replace_and_delete_use_optimistic_binding_version(tmp_path, monkeypatch):
    user_id = "creator-one"
    ledger = _ledger(tmp_path, monkeypatch, user_id, "voice-house")
    first = _profile(ledger, user_id, uses=["speech"])
    second = _profile(ledger, user_id, uses=["speech"])
    _install_fake_studio(monkeypatch)
    service = LiveVoiceBindingService(_store(tmp_path))

    token = set_current_user_id(user_id)
    try:
        initial = service.bind(
            user_id,
            "session-one",
            "mic-one",
            LiveVoiceBindingUpsert(chat2_project_name="voice-house", profile_id=first.id, purpose="speech"),
        )
        with pytest.raises(StudioConflict):
            service.bind(
                user_id,
                "session-one",
                "mic-one",
                LiveVoiceBindingUpsert(chat2_project_name="voice-house", profile_id=second.id, purpose="speech"),
            )
        replaced = service.bind(
            user_id,
            "session-one",
            "mic-one",
            LiveVoiceBindingUpsert(
                chat2_project_name="voice-house",
                profile_id=second.id,
                purpose="speech",
                expected_version=initial["version"],
            ),
        )
        with pytest.raises(StudioConflict):
            service.unbind(user_id, "session-one", "mic-one", initial["version"])
        removed = service.unbind(user_id, "session-one", "mic-one", replaced["version"])
    finally:
        reset_current_user_id(token)

    assert replaced["version"] == initial["version"] + 1
    assert removed["removed"] is True
    assert removed["processor_runtime_changed"] is False
    assert removed["programme_state_changed"] is False


def test_binding_installer_mounts_routes_exactly_once():
    app = FastAPI()
    install_shared_skies_live_voice_bindings(app)
    install_shared_skies_live_voice_bindings(app)

    paths = [
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.router.routes
        if "/voice/bindings" in getattr(route, "path", "")
    ]
    assert paths.count(("/shared-sky/studio/api/sessions/{session_id}/voice/bindings", ("GET",))) == 1
    assert paths.count(("/shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}", ("PUT",))) == 1
    assert paths.count(("/shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}", ("DELETE",))) == 1


def test_delete_request_requires_version():
    with pytest.raises(Exception):
        LiveVoiceBindingDelete()

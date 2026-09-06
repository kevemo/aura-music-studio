from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from aura_music_studio import tenant_storage
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.rights import RightsLedger, VoiceProfile
from aura_music_studio.shared_sky_control_room import StudioConflict, StudioInvariantError
from aura_music_studio import shared_skies_live_voice_bindings as bindings


def _binding_store(tmp_path) -> bindings.LiveVoiceBindingStore:
    db_path = tmp_path / "live-bindings.sqlite"
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE shared_sky_studio_sessions(id TEXT PRIMARY KEY)")
        con.execute("CREATE TABLE shared_sky_sources(id TEXT PRIMARY KEY)")
        con.execute("INSERT INTO shared_sky_studio_sessions(id) VALUES('session-1')")
        con.execute("INSERT INTO shared_sky_sources(id) VALUES('source-1')")
    return bindings.LiveVoiceBindingStore(db_path)


def _project_ledger(tmp_path, monkeypatch, user_id: str, project_name: str) -> RightsLedger:
    monkeypatch.setattr(tenant_storage, "ROOT", tmp_path.resolve())
    token = set_current_user_id(user_id)
    try:
        project = tenant_storage.project_path(project_name, must_exist=False)
        project.mkdir(parents=True, exist_ok=True)
        return RightsLedger(project / ".aura_rights")
    finally:
        reset_current_user_id(token)


def _save_profile(ledger: RightsLedger, user_id: str, *, uses: list[str] | None = None) -> VoiceProfile:
    token = set_current_user_id(user_id)
    try:
        return ledger.save_voice(
            VoiceProfile(
                name="Approved LIVE Voice",
                owner_label="Creator",
                reference_files=["/private/reference.wav"],
                consent_confirmed=True,
                consent_statement="I explicitly authorise this voice profile for the declared uses.",
                verification_state="attested",
                allowed_uses=uses or ["speech"],
            )
        )
    finally:
        reset_current_user_id(token)


class _FakeRepo:
    def __init__(self, *, project_id: str = "project-1"):
        self.project_id = project_id

    def get_session(self, user_id: str, session_id: str):
        if user_id != "creator-one" or session_id != "session-1":
            raise KeyError(session_id)
        return {
            "id": session_id,
            "project_id": self.project_id,
            "broadcast_id": "broadcast-1",
            "version": 7,
        }


class _FakeGraph:
    def __init__(self, *, project_id: str = "project-1", source_type: str = "microphone"):
        self.project_id = project_id
        self.source_type = source_type
        self.events: list[tuple[str, str | None, str, dict]] = []

    def source(self, user_id: str, source_id: str):
        if user_id != "creator-one" or source_id != "source-1":
            raise KeyError(source_id)
        return {
            "id": source_id,
            "project_id": self.project_id,
            "source_type": self.source_type,
            "name": "Host microphone",
            "visible": True,
            "config": {"privacy": "programme_safe", "audio": {"muted": False}},
        }

    def event(self, user_id: str, broadcast_id: str | None, event_type: str, payload: dict | None = None):
        self.events.append((user_id, broadcast_id, event_type, dict(payload or {})))


def _install_fakes(monkeypatch, store, *, source_project="project-1", source_type="microphone"):
    graph = _FakeGraph(project_id=source_project, source_type=source_type)
    monkeypatch.setattr(bindings, "binding_store", store)
    monkeypatch.setattr(bindings, "studio_repo", _FakeRepo())
    monkeypatch.setattr(bindings, "studio", SimpleNamespace(graph=graph))
    return graph


def test_binding_persists_only_authoritative_references_and_never_activates_processor(tmp_path, monkeypatch):
    user_id = "creator-one"
    voice_project = "voice-house"
    ledger = _project_ledger(tmp_path, monkeypatch, user_id, voice_project)
    profile = _save_profile(ledger, user_id)
    store = _binding_store(tmp_path)
    graph = _install_fakes(monkeypatch, store)

    token = set_current_user_id(user_id)
    try:
        result = bindings.bind_live_voice_profile(
            user_id,
            "session-1",
            "source-1",
            bindings.LiveVoiceBindingUpsert(
                chat2_project_name=voice_project,
                profile_id=profile.id,
                purpose="speech",
            ),
        )
    finally:
        reset_current_user_id(token)

    assert result["version"] == 1
    assert result["binding_state"] == "authorised_reference_only"
    assert result["profile_id"] == profile.id
    assert result["profile_data_copied_into_chat5"] is False
    assert result["processor_runtime_attached"] is False
    assert result["real_time_processing_proven"] is False
    assert result["processor_activation_allowed_by_binding_alone"] is False
    assert result["commercial_entitlement_state"] == "not_evaluated_chat6_authority"
    assert "/private/reference.wav" not in repr(result)
    assert graph.events[-1][2] == "studio_live_voice_profile_bound"


def test_binding_rebind_requires_exact_optimistic_version(tmp_path, monkeypatch):
    user_id = "creator-one"
    voice_project = "voice-house"
    ledger = _project_ledger(tmp_path, monkeypatch, user_id, voice_project)
    profile = _save_profile(ledger, user_id)
    store = _binding_store(tmp_path)
    _install_fakes(monkeypatch, store)

    token = set_current_user_id(user_id)
    try:
        first = bindings.bind_live_voice_profile(
            user_id,
            "session-1",
            "source-1",
            bindings.LiveVoiceBindingUpsert(
                chat2_project_name=voice_project,
                profile_id=profile.id,
                purpose="speech",
            ),
        )
        second = bindings.bind_live_voice_profile(
            user_id,
            "session-1",
            "source-1",
            bindings.LiveVoiceBindingUpsert(
                chat2_project_name=voice_project,
                profile_id=profile.id,
                purpose="speech",
                expected_binding_version=first["version"],
            ),
        )
        with pytest.raises(StudioConflict):
            bindings.bind_live_voice_profile(
                user_id,
                "session-1",
                "source-1",
                bindings.LiveVoiceBindingUpsert(
                    chat2_project_name=voice_project,
                    profile_id=profile.id,
                    purpose="speech",
                    expected_binding_version=first["version"],
                ),
            )
    finally:
        reset_current_user_id(token)

    assert second["version"] == 2


def test_binding_rejects_cross_project_and_non_audio_sources(tmp_path, monkeypatch):
    user_id = "creator-one"
    voice_project = "voice-house"
    ledger = _project_ledger(tmp_path, monkeypatch, user_id, voice_project)
    profile = _save_profile(ledger, user_id)
    store = _binding_store(tmp_path)

    token = set_current_user_id(user_id)
    try:
        _install_fakes(monkeypatch, store, source_project="another-project")
        with pytest.raises(StudioInvariantError, match="does not belong"):
            bindings.bind_live_voice_profile(
                user_id,
                "session-1",
                "source-1",
                bindings.LiveVoiceBindingUpsert(
                    chat2_project_name=voice_project,
                    profile_id=profile.id,
                    purpose="speech",
                ),
            )

        _install_fakes(monkeypatch, store, source_type="image")
        with pytest.raises(StudioInvariantError, match="audio-bearing"):
            bindings.bind_live_voice_profile(
                user_id,
                "session-1",
                "source-1",
                bindings.LiveVoiceBindingUpsert(
                    chat2_project_name=voice_project,
                    profile_id=profile.id,
                    purpose="speech",
                ),
            )
    finally:
        reset_current_user_id(token)


def test_revoked_profile_invalidates_binding_and_final_execution_fails_closed(tmp_path, monkeypatch):
    user_id = "creator-one"
    voice_project = "voice-house"
    ledger = _project_ledger(tmp_path, monkeypatch, user_id, voice_project)
    profile = _save_profile(ledger, user_id)
    store = _binding_store(tmp_path)
    _install_fakes(monkeypatch, store)

    token = set_current_user_id(user_id)
    try:
        bindings.bind_live_voice_profile(
            user_id,
            "session-1",
            "source-1",
            bindings.LiveVoiceBindingUpsert(
                chat2_project_name=voice_project,
                profile_id=profile.id,
                purpose="speech",
            ),
        )
        ledger.revoke_voice(profile.id, "Consent withdrawn")
        current = bindings.live_voice_bindings(user_id, "session-1")
        with pytest.raises(PermissionError):
            bindings.authorize_live_voice_binding_for_execution(user_id, "session-1", "source-1")
    finally:
        reset_current_user_id(token)

    assert current[0]["binding_state"] == "invalidated"
    assert current[0]["current_chat2_authorisation"] == "invalidated_or_unavailable"
    assert current[0]["processor_activation_allowed_by_binding_alone"] is False


def test_final_execution_reauthorises_chat2_and_marks_profile_used(tmp_path, monkeypatch):
    user_id = "creator-one"
    voice_project = "voice-house"
    ledger = _project_ledger(tmp_path, monkeypatch, user_id, voice_project)
    profile = _save_profile(ledger, user_id)
    store = _binding_store(tmp_path)
    _install_fakes(monkeypatch, store)

    token = set_current_user_id(user_id)
    try:
        bindings.bind_live_voice_profile(
            user_id,
            "session-1",
            "source-1",
            bindings.LiveVoiceBindingUpsert(
                chat2_project_name=voice_project,
                profile_id=profile.id,
                purpose="speech",
            ),
        )
        authorised = bindings.authorize_live_voice_binding_for_execution(user_id, "session-1", "source-1")
        reloaded = ledger.get_voice(profile.id)
    finally:
        reset_current_user_id(token)

    assert authorised.id == profile.id
    assert reloaded.last_used_at is not None


def test_unbind_requires_exact_version_and_emits_reference_event(tmp_path, monkeypatch):
    user_id = "creator-one"
    voice_project = "voice-house"
    ledger = _project_ledger(tmp_path, monkeypatch, user_id, voice_project)
    profile = _save_profile(ledger, user_id)
    store = _binding_store(tmp_path)
    graph = _install_fakes(monkeypatch, store)

    token = set_current_user_id(user_id)
    try:
        bound = bindings.bind_live_voice_profile(
            user_id,
            "session-1",
            "source-1",
            bindings.LiveVoiceBindingUpsert(
                chat2_project_name=voice_project,
                profile_id=profile.id,
                purpose="speech",
            ),
        )
        with pytest.raises(StudioConflict):
            bindings.unbind_live_voice_profile(user_id, "session-1", "source-1", bound["version"] + 1)
        removed = bindings.unbind_live_voice_profile(user_id, "session-1", "source-1", bound["version"])
        with pytest.raises(KeyError):
            store.get(user_id, "session-1", "source-1")
    finally:
        reset_current_user_id(token)

    assert removed["profile_id"] == profile.id
    assert graph.events[-1][2] == "studio_live_voice_profile_unbound"


def test_binding_routes_mount_exactly_once():
    app = FastAPI()
    bindings.install_shared_skies_live_voice_bindings(app)
    bindings.install_shared_skies_live_voice_bindings(app)

    expected = {
        ("GET", "/shared-sky/studio/api/sessions/{session_id}/voice/bindings"),
        ("PUT", "/shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}"),
        ("DELETE", "/shared-sky/studio/api/sessions/{session_id}/voice/bindings/{source_id}"),
    }
    found = []
    for route in app.router.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            pair = (str(method).upper(), path)
            if pair in expected:
                found.append(pair)

    assert sorted(found) == sorted(expected)

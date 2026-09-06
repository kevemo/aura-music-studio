from __future__ import annotations

from fastapi import FastAPI
import pytest

from aura_music_studio import tenant_storage
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.rights import RightsLedger, VoiceProfile
from aura_music_studio.shared_skies_live_voice_profiles import (
    _tenant_rights_root,
    authorised_profiles_for_live,
    install_shared_skies_live_voice_profiles,
)


def _project_ledger(tmp_path, monkeypatch, user_id: str, project_name: str) -> RightsLedger:
    monkeypatch.setattr(tenant_storage, "ROOT", tmp_path.resolve())
    token = set_current_user_id(user_id)
    try:
        project = tenant_storage.project_path(project_name, must_exist=False)
        project.mkdir(parents=True, exist_ok=True)
        return RightsLedger(project / ".aura_rights")
    finally:
        reset_current_user_id(token)


def _save_profile(ledger: RightsLedger, user_id: str, *, name: str, uses: list[str]) -> VoiceProfile:
    token = set_current_user_id(user_id)
    try:
        return ledger.save_voice(
            VoiceProfile(
                name=name,
                owner_label=f"{name} owner",
                reference_files=[f"/private/{name}.wav"],
                consent_confirmed=True,
                consent_statement="I explicitly authorise this voice profile for the declared uses.",
                verification_state="attested",
                allowed_uses=uses,
            )
        )
    finally:
        reset_current_user_id(token)


def test_discovery_returns_only_currently_authorised_profiles_without_raw_references(tmp_path, monkeypatch):
    user_id = "creator-one"
    project_name = "voice-house"
    ledger = _project_ledger(tmp_path, monkeypatch, user_id, project_name)
    speech = _save_profile(ledger, user_id, name="Speech", uses=["speech", "dubbing"])
    _save_profile(ledger, user_id, name="Singer", uses=["singing"])
    revoked = _save_profile(ledger, user_id, name="Revoked", uses=["speech"])

    token = set_current_user_id(user_id)
    try:
        ledger.revoke_voice(revoked.id, "Consent withdrawn")
        rows = authorised_profiles_for_live(user_id, project_name, "speech")
    finally:
        reset_current_user_id(token)

    assert [row["profile_id"] for row in rows] == [speech.id]
    row = rows[0]
    assert row["purpose_authorised"] is True
    assert row["live_binding"] == "candidate_only"
    assert row["real_time_capability"] is False
    assert row["entitlement_state"] == "not_evaluated_chat6_authority"
    assert row["raw_reference_files_exposed"] is False
    assert row["model_or_provider_secrets_exposed"] is False
    assert "/private/Speech.wav" not in repr(row)


def test_tenant_context_mismatch_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant_storage, "ROOT", tmp_path.resolve())
    token = set_current_user_id("other-user")
    try:
        with pytest.raises(PermissionError):
            _tenant_rights_root("creator-one", "voice-house")
    finally:
        reset_current_user_id(token)


def test_same_project_name_in_another_tenant_is_not_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant_storage, "ROOT", tmp_path.resolve())
    ledger_a = _project_ledger(tmp_path, monkeypatch, "creator-a", "voice-house")
    ledger_b = _project_ledger(tmp_path, monkeypatch, "creator-b", "voice-house")
    profile_a = _save_profile(ledger_a, "creator-a", name="A", uses=["speech"])
    _save_profile(ledger_b, "creator-b", name="B", uses=["speech"])

    token = set_current_user_id("creator-a")
    try:
        rows = authorised_profiles_for_live("creator-a", "voice-house", "speech")
    finally:
        reset_current_user_id(token)

    assert [row["profile_id"] for row in rows] == [profile_a.id]
    assert [row["profile_name"] for row in rows] == ["A"]


def test_installer_mounts_profile_discovery_route_exactly_once():
    app = FastAPI()
    install_shared_skies_live_voice_profiles(app)
    install_shared_skies_live_voice_profiles(app)

    matches = [
        route
        for route in app.router.routes
        if getattr(route, "path", "") == "/shared-sky/studio/api/sessions/{session_id}/voice/profiles"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    assert len(matches) == 1

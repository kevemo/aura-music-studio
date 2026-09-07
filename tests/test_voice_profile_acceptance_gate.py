from __future__ import annotations

import math

import numpy as np
import pytest
import soundfile as sf

from aura_music_studio import voice_house_api
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.rights import RightsLedger, VoiceProfile, authorize_voice_profile
from aura_music_studio.voice import analyze_voice_sample, create_voice_profile
from aura_music_studio.voice_profile_lifecycle import router as voice_profile_lifecycle_router


def _tone(path, *, seconds: float = 1.25, sample_rate: int = 24000, amplitude: float = 0.25):
    frames = max(1, int(seconds * sample_rate))
    t = np.arange(frames, dtype=np.float64) / sample_rate
    audio = amplitude * np.sin(2.0 * math.pi * 220.0 * t)
    sf.write(path, audio, sample_rate, subtype="PCM_16")
    return path


def _authorised_profile(name: str = "Private Voice") -> VoiceProfile:
    return VoiceProfile(
        name=name,
        owner_label="Owner",
        consent_confirmed=True,
        consent_statement="I authorise this voice profile for my own private music project.",
        subject_relationship="self",
    )


def test_challenge_created_profile_migrates_to_explicit_self_relationship_and_timestamp():
    profile = VoiceProfile(
        name="Challenge Voice",
        owner_label="Voice Owner",
        consent_confirmed=True,
        consent_statement="I explicitly authorise this private voice profile for my selected uses.",
        metadata={"challenge_id": "challenge-123"},
    )
    assert profile.subject_relationship == "self"
    assert profile.consent_recorded_at == profile.created_at
    assert profile.active is True


def test_other_authorized_person_gets_distinct_owner_recording_challenge(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_house_api, "_project", lambda _name: tmp_path)
    result = voice_house_api.create_voice_challenge("demo", "other_authorized_person")
    assert result["subject_relationship"] == "other_authorized_person"
    assert "other authorised voice owner" in result["instruction"].lower()
    rows = voice_house_api._read_challenges(tmp_path)
    stored = next(row for row in rows if row["id"] == result["challenge_id"])
    assert stored["subject_relationship"] == "other_authorized_person"


def test_voice_challenge_rejects_unknown_subject_relationship(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_house_api, "_project", lambda _name: tmp_path)
    with pytest.raises(Exception) as exc:
        voice_house_api.create_voice_challenge("demo", "public_celebrity")
    assert getattr(exc.value, "status_code", None) == 400


def test_authenticated_save_binds_profile_to_tenant_and_rejects_cross_tenant_write(tmp_path):
    ledger = RightsLedger(tmp_path / "rights")
    token = set_current_user_id("member-a")
    try:
        profile = ledger.save_voice(_authorised_profile())
    finally:
        reset_current_user_id(token)

    assert profile.tenant_user_id == "member-a"
    assert profile.created_by_user_id == "member-a"

    token = set_current_user_id("member-b")
    try:
        with pytest.raises(PermissionError, match="another tenant"):
            ledger.save_voice(profile)
        with pytest.raises(PermissionError, match="another tenant"):
            authorize_voice_profile(ledger.root, profile.id, "singing")
    finally:
        reset_current_user_id(token)


def test_revoke_route_hides_and_rejects_cross_tenant_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_house_api, "_project", lambda _name: tmp_path)
    ledger = RightsLedger(tmp_path / ".aura_rights")
    token = set_current_user_id("member-a")
    try:
        profile = ledger.save_voice(_authorised_profile("Member A Voice"))
    finally:
        reset_current_user_id(token)

    token = set_current_user_id("member-b")
    try:
        with pytest.raises(Exception) as exc:
            voice_house_api.revoke_voice_house_profile(
                "demo",
                profile.id,
                voice_house_api.RevokeVoiceRequest(reason="malicious cross-tenant revoke"),
            )
        assert getattr(exc.value, "status_code", None) == 404
    finally:
        reset_current_user_id(token)

    assert ledger.get_voice(profile.id).active is True


def test_private_library_filters_explicit_cross_tenant_records(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_house_api, "_project", lambda _name: tmp_path)
    ledger = RightsLedger(tmp_path / ".aura_rights")

    token = set_current_user_id("member-a")
    try:
        member_a = ledger.save_voice(_authorised_profile("Member A Voice"))
    finally:
        reset_current_user_id(token)

    token = set_current_user_id("member-b")
    try:
        member_b = ledger.save_voice(_authorised_profile("Member B Voice"))
        listing = voice_house_api.voice_house_profiles("demo")
    finally:
        reset_current_user_id(token)

    ids = {profile["id"] for profile in listing["profiles"]}
    assert member_b.id in ids
    assert member_a.id not in ids
    assert listing["private_library"] is True
    assert listing["raw_reference_paths_exposed"] is False


def test_legacy_upload_path_creates_locked_candidate_not_reusable_identity_profile(tmp_path):
    voice = _tone(tmp_path / "voice.wav")
    ledger = RightsLedger(tmp_path / "rights")
    token = set_current_user_id("member-a")
    try:
        profile = create_voice_profile(
            ledger,
            name="Legacy Candidate",
            owner_label="Owner",
            reference_files=[voice],
            consent_statement="I confirm I own this recording, but this old path has no recorded challenge.",
        )
        assert profile.active is False
        assert profile.consent_confirmed is False
        assert profile.verification_state == "unverified"
        assert profile.metadata["identity_profile_locked"] is True
        assert profile.metadata["requires_voice_house_challenge"] is True
        with pytest.raises(PermissionError, match="locked"):
            authorize_voice_profile(ledger.root, profile.id, "voice_conversion")
    finally:
        reset_current_user_id(token)


def test_reference_quality_gate_accepts_real_voice_band_audio_and_records_quality(tmp_path):
    scan = analyze_voice_sample(_tone(tmp_path / "valid.wav"))
    assert scan["quality_state"] == "accepted"
    assert scan["duration_seconds"] >= 1.0
    assert scan["sample_rate"] == 24000
    assert scan["voiced_ratio"] >= 0.02
    assert scan["rms"] > 0.0
    assert scan["peak"] > 0.0


def test_reference_quality_gate_rejects_too_short_silent_and_low_rate_audio(tmp_path):
    with pytest.raises(ValueError, match="at least 1 second"):
        analyze_voice_sample(_tone(tmp_path / "short.wav", seconds=0.25))

    silent = tmp_path / "silent.wav"
    sf.write(silent, np.zeros(24000, dtype=np.float64), 24000, subtype="PCM_16")
    with pytest.raises(ValueError, match="effectively silent|insufficient detectable"):
        analyze_voice_sample(silent)

    with pytest.raises(ValueError, match="at least 16 kHz"):
        analyze_voice_sample(_tone(tmp_path / "low-rate.wav", sample_rate=8000))


def test_voice_profile_version_rename_revoke_delete_lifecycle(tmp_path):
    ledger = RightsLedger(tmp_path / "rights")
    profile = ledger.save_voice(
        VoiceProfile(
            name="Original",
            owner_label="Owner",
            consent_confirmed=True,
            consent_statement="I authorise this profile and understand I can revoke or delete it.",
            subject_relationship="self",
        )
    )
    renamed = ledger.rename_voice(profile.id, "Renamed")
    assert renamed.name == "Renamed"
    assert renamed.version == 2

    revoked = ledger.revoke_voice(profile.id, "Consent withdrawn")
    assert revoked.active is False
    assert revoked.version == 3
    assert revoked.revoked_at

    deleted = ledger.delete_voice(profile.id)
    assert deleted.id == profile.id
    with pytest.raises(KeyError):
        ledger.get_voice(profile.id)


def test_voice_profile_lifecycle_router_defines_each_operation_once():
    """Assert the lifecycle router itself is deterministic.

    The production overlay composition is independently exercised by the self-host route-surface
    smoke. Keeping this unit assertion on the owning router avoids suite-order coupling when
    application-level tests temporarily mutate/combine router objects during collection.
    """
    routes = [
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in voice_profile_lifecycle_router.routes
    ]
    expected = "/projects/{project_name}/voice-house/profiles/{profile_id}"
    assert routes.count((expected, ("GET",))) == 1
    assert routes.count((expected, ("PATCH",))) == 1
    assert routes.count((expected, ("DELETE",))) == 1

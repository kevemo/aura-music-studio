from __future__ import annotations

import yaml
import pytest

from aura_music_studio.content_safety import enforce_creation_policy, public_policy_summary
from aura_music_studio.creation import CreateSongRequest, build_song_project
from aura_music_studio.creative_ip_policy import (
    POLICY_VERSION,
    evaluate_ip_text,
    public_ip_policy_summary,
    require_input_rights,
)


def test_direct_artist_imitation_is_blocked():
    decision = evaluate_ip_text("Make this song sound exactly like Taylor Swift")
    assert decision.allowed is False
    assert decision.category == "direct_artist_or_creator_imitation"
    assert decision.policy_version == POLICY_VERSION
    assert "genre" in decision.safe_alternative.lower()


def test_existing_song_reproduction_is_blocked():
    decision = evaluate_ip_text("Recreate the original copyrighted song recording exactly")
    assert decision.allowed is False
    assert decision.category == "existing_work_reproduction"


def test_existing_lyrics_copy_request_is_blocked():
    decision = evaluate_ip_text("Use the lyrics from an existing hit song")
    assert decision.allowed is False
    assert decision.category == "existing_lyrics_reproduction"


def test_unauthorized_voice_clone_request_is_blocked():
    decision = evaluate_ip_text("Clone the voice of a famous singer for my new track")
    assert decision.allowed is False
    assert decision.category == "unauthorized_voice_or_likeness"


def test_neutral_creative_attributes_are_allowed():
    decision = evaluate_ip_text(
        "Create an original 96 BPM synth-pop song with warm analog pads, punchy live-feel drums, "
        "a clear alto vocal, a bittersweet chorus and wide cinematic ambience"
    )
    assert decision.allowed is True


def test_existing_creation_policy_inherits_ip_firewall():
    with pytest.raises(ValueError, match="direct_artist_or_creator_imitation"):
        enforce_creation_policy(
            "Produce exactly in the style of a named real artist",
            context="Music creation",
        )


def test_user_lyrics_require_explicit_rights_confirmation(tmp_path):
    request = CreateSongRequest(
        title="Original Song",
        lyrics="These are user supplied lyrics",
        lyrics_rights_confirmed=False,
    )
    with pytest.raises(ValueError, match="own or have permission/license to use the lyrics"):
        build_song_project(request, tmp_path)


def test_reference_audio_requires_explicit_rights_confirmation(tmp_path):
    request = CreateSongRequest(
        title="Reference Song",
        reference_audio="/tmp/reference.wav",
        reference_audio_rights_confirmed=False,
    )
    with pytest.raises(ValueError, match="audio/reference recording"):
        build_song_project(request, tmp_path)


def test_approved_voice_requires_consent_profile(tmp_path):
    request = CreateSongRequest(
        title="Voice Song",
        vocal_mode="approved_voice",
        voice_profile_id=None,
    )
    with pytest.raises(ValueError, match="consent-approved Aura Voice Profile"):
        build_song_project(request, tmp_path)


def test_song_manifest_records_rights_clearance(tmp_path):
    request = CreateSongRequest(
        title="Rights Cleared Song",
        lyrics="Original words written for this test",
        lyrics_rights_confirmed=True,
        extra_prompt="original acoustic pop, gentle piano and human-feel drums",
    )
    project = build_song_project(request, tmp_path)
    manifest = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    clearance = manifest["rights_clearance"]
    assert manifest["rights_confirmed"] is True
    assert clearance["policy_version"] == POLICY_VERSION
    assert clearance["user_lyrics"]["provided"] is True
    assert clearance["user_lyrics"]["rights_confirmed"] is True
    assert clearance["direct_imitation_prohibited"] is True
    assert clearance["commercial_use_is_not_a_copyright_guarantee"] is True
    assert clearance["automatic_legal_clearance"] is False
    assert manifest["project_dna"]["voice_profile_id"] is None


def test_rights_requirement_is_noop_when_input_not_provided():
    require_input_rights("lyrics", provided=False, rights_confirmed=False)


def test_public_policy_does_not_claim_legal_clearance():
    ip_summary = public_ip_policy_summary()
    combined = public_policy_summary()
    assert ip_summary["automatic_legal_clearance"] is False
    assert combined["creative_ip"]["automatic_legal_clearance"] is False

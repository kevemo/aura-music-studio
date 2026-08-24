from __future__ import annotations

import pytest

from aura_music_studio.rights import RightsLedger, VoiceProfile


def test_legacy_consent_statement_migrates_only_to_attested():
    profile = VoiceProfile(
        name="My Voice",
        owner_label="Voice Owner",
        consent_confirmed=True,
        consent_statement="I consent to this voice profile being used for my own authorised music project.",
        allowed_uses=["singing", "voice_conversion"],
    )
    assert profile.verification_state == "attested"
    assert profile.verification_method == "consent_statement_attestation"
    assert profile.active is True
    profile.assert_usable("singing")


def test_attested_profile_does_not_gain_unbounded_similarity_by_default():
    profile = VoiceProfile(
        name="Attested Voice",
        owner_label="Owner",
        consent_confirmed=True,
        consent_statement="I confirm I own this voice and authorise the selected project uses.",
    )
    assert profile.verification_state == "attested"
    assert profile.similarity_limit == 0.8


def test_phrase_match_alone_does_not_claim_verified_speaker_identity():
    profile = VoiceProfile(
        name="Phrase Match",
        owner_label="Owner",
        consent_confirmed=True,
        consent_statement="I authorise this voice profile and have recorded the requested verification phrase.",
        verification_state="verified",
        verification_method="local_stt_phrase_match",
        verification_confidence=0.99,
        similarity_limit=1.0,
        metadata={},
    )
    assert profile.verification_state == "attested"
    assert profile.similarity_limit == 0.8
    assert profile.metadata["phrase_verification_method"] == "local_stt_phrase_match"


def test_trusted_speaker_plus_phrase_method_can_retain_verified_state():
    profile = VoiceProfile(
        name="Speaker Verified",
        owner_label="Owner",
        consent_confirmed=True,
        consent_statement="I authorise this verified voice profile for the explicitly selected uses.",
        verification_state="verified",
        verification_method="speaker_verification_plus_phrase_match",
        verification_confidence=0.94,
        similarity_limit=0.95,
    )
    assert profile.verification_state == "verified"
    assert profile.similarity_limit == 0.95


def test_voice_revocation_fails_closed_for_all_uses(tmp_path):
    ledger = RightsLedger(tmp_path / "rights")
    profile = ledger.save_voice(
        VoiceProfile(
            name="Revocable Voice",
            owner_label="Owner",
            consent_confirmed=True,
            consent_statement="I consent to voice conversion, harmonies and singing in this project.",
            allowed_uses=["singing", "backing_harmony", "voice_conversion"],
        )
    )
    profile.assert_usable("voice_conversion")

    revoked = ledger.revoke_voice(profile.id, "Consent withdrawn by voice owner")
    assert revoked.active is False
    assert revoked.verification_state == "revoked"
    assert revoked.consent_confirmed is False
    assert revoked.revoked_at

    reloaded = ledger.get_voice(profile.id)
    for use in ("singing", "backing_harmony", "voice_conversion"):
        with pytest.raises(PermissionError, match="revoked"):
            reloaded.assert_usable(use)


def test_profile_rejects_use_not_explicitly_authorised():
    profile = VoiceProfile(
        name="Singing Only",
        owner_label="Owner",
        consent_confirmed=True,
        consent_statement="I authorise this voice profile only for singing in my own project.",
        allowed_uses=["singing"],
    )
    profile.assert_usable("singing")
    with pytest.raises(PermissionError, match="not approved"):
        profile.assert_usable("dubbing")

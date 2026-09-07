from __future__ import annotations

from aura_music_studio.access_control import _required_feature
from aura_music_studio.plans import APPROVED_VOICE_DUPLICATION, get_plan


def test_voice_house_identity_creation_requires_approved_voice_duplication():
    assert (
        _required_feature("/projects/song-a/voice-house/challenge", "POST")
        == APPROVED_VOICE_DUPLICATION
    )
    assert (
        _required_feature("/projects/song-a/voice-house/profiles", "POST")
        == APPROVED_VOICE_DUPLICATION
    )


def test_voice_house_consent_safety_controls_are_not_paywalled_after_downgrade():
    profile = "/projects/song-a/voice-house/profiles/profile-123"
    assert _required_feature("/projects/song-a/voice-house/profiles", "GET") is None
    assert _required_feature(profile, "GET") is None
    assert _required_feature(profile, "PATCH") is None
    assert _required_feature(profile, "DELETE") is None
    assert _required_feature(profile + "/revoke", "POST") is None


def test_voice_duplication_feature_remains_pro_only():
    assert not get_plan("free").has(APPROVED_VOICE_DUPLICATION)
    assert not get_plan("base").has(APPROVED_VOICE_DUPLICATION)
    assert get_plan("pro").has(APPROVED_VOICE_DUPLICATION)


def test_existing_voice_execution_routes_keep_premium_boundary():
    assert _required_feature("/projects/song-a/voice-convert", "POST") == APPROVED_VOICE_DUPLICATION
    assert _required_feature("/projects/song-a/voice-profiles", "POST") == APPROVED_VOICE_DUPLICATION
    assert _required_feature("/projects/song-a/voices", "GET") == APPROVED_VOICE_DUPLICATION

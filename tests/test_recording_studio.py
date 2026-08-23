from __future__ import annotations

from aura_music_studio.plans import UPLOAD_AUDIO, get_plan
from aura_music_studio.recording_api import ALLOWED_ROLES, _safe_name


def test_recording_unlocks_from_base_upward():
    assert not get_plan("free").has(UPLOAD_AUDIO)
    assert get_plan("base").has(UPLOAD_AUDIO)
    assert get_plan("pro").has(UPLOAD_AUDIO)


def test_recording_roles_cover_vocal_and_instrument_sources():
    for role in ("vocals", "backing_vocals", "guitar", "bass", "drums", "piano", "keyboard", "synth", "strings", "percussion"):
        assert role in ALLOWED_ROLES


def test_recording_title_cannot_escape_project_via_filename():
    value = _safe_name("../../private/../Kev Vocal <take 1>")
    assert "/" not in value
    assert "\\" not in value
    assert "<" not in value
    assert ">" not in value
    assert len(value) <= 100

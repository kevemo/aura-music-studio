import pytest

from aura_music_studio.aura_daw_tools import _clip, _track
from aura_music_studio.session import Clip, StudioSession


def _session():
    session = StudioSession(name="Test")
    session.add_track("Master", "master")
    vocals = session.add_track("Lead Vocal", "vocals")
    vocals.clips.append(Clip(name="Lead Take", kind="audio", source="input/vocal.wav", duration=10.0))
    guitar = session.add_track("Acoustic Guitar", "guitar")
    guitar.clips.append(Clip(name="Guitar Verse", kind="audio", source="input/guitar.wav", duration=10.0))
    return session


def test_track_selector_resolves_role_and_partial_name():
    session = _session()
    assert _track(session, "vocals").name == "Lead Vocal"
    assert _track(session, "Acoustic").role == "guitar"


def test_clip_selector_resolves_partial_name():
    session = _session()
    track, clip = _clip(session, "Verse")
    assert track.role == "guitar"
    assert clip.name == "Guitar Verse"


def test_track_selector_refuses_ambiguous_matches():
    session = _session()
    session.add_track("Backing Vocal", "backing_vocals")
    with pytest.raises(ValueError):
        _track(session, "vocal")


def test_missing_track_is_not_guessed():
    with pytest.raises(KeyError):
        _track(_session(), "orchestra")

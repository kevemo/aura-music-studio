from pathlib import Path

import pytest

from aura_music_studio.assets import AssetLibrary
from aura_music_studio.models import ProjectManifest, RendererConfig
from aura_music_studio.producer import rule_based_plan
from aura_music_studio.session import StudioSession


def test_real_audio_manifest_rejects_symbolic_final():
    with pytest.raises(ValueError):
        ProjectManifest(
            project_name="x",
            title="x",
            mode="original",
            rights_confirmed=True,
            renderer=RendererConfig(require_real_audio=True, allow_symbolic_guide_as_final=True),
        )


def test_asset_detection():
    assert AssetLibrary.detect_kind(Path("song.wav")) == "audio"
    assert AssetLibrary.detect_kind(Path("score.pdf")) == "score"
    assert AssetLibrary.detect_kind(Path("notes.mid")) == "symbolic"
    assert AssetLibrary.detect_kind(Path("lyrics.txt")) == "text"


def test_producer_replace_parses_time_range():
    plan = rule_based_plan("replace the guitar from 1:20 to 1:35 and make it more expressive")
    action = plan.actions[0]
    assert action.action == "replace_region"
    assert action.track_role == "guitar"
    assert action.start_seconds == 80
    assert action.end_seconds == 95
    assert plan.needs_confirmation is False


def test_session_take_lanes():
    session = StudioSession(name="test")
    track = session.add_track("Guitar", "guitar")
    assert session.find_track(track.id).name == "Guitar"
    assert session.tracks[0].clips == []

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from aura_music_studio.assets import AssetLibrary
from aura_music_studio.creation import CreateSongRequest, build_song_project
from aura_music_studio.models import ProjectManifest, RendererConfig
from aura_music_studio.presets import get_preset
from aura_music_studio.producer import rule_based_plan
from aura_music_studio.samples import analyze_sample
from aura_music_studio.session import StudioSession
from aura_music_studio.styles import StyleBlend, StyleReference, build_style_dna


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


def test_genre_presets_include_real_performance_language():
    rock = get_preset("rock")
    assert "live drums" in rock.instruments
    assert rock.master_preset == "rock"


def test_new_song_manifest_keeps_real_audio_gate_and_genre_master(tmp_path):
    project = build_song_project(CreateSongRequest(title="Test Rock", genre="rock", vocal_mode="instrumental"), tmp_path)
    import yaml
    data = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    assert data["renderer"]["require_real_audio"] is True
    assert data["renderer"]["allow_symbolic_guide_as_final"] is False
    assert data["mix"]["mastering_preset"] == "rock"
    assert data["mix"]["export_flac"] is True
    assert data["project_dna"]["genre_preset"]["genre"] == "rock"


def _tone(path: Path, freq: float, seconds: float = 1.0):
    sr = 22050
    t = np.arange(int(sr * seconds), dtype=np.float32) / sr
    y = (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sf.write(path, y, sr)


def test_sample_analysis_is_waveform_based(tmp_path):
    wav = tmp_path / "sample.wav"
    _tone(wav, 440.0)
    report = analyze_sample(wav)
    assert report.duration_seconds == pytest.approx(1.0, abs=.02)
    assert report.sample_rate == 22050
    assert report.peak_dbfs < 0


def test_weighted_style_dna(tmp_path):
    a = tmp_path / "a.wav"; b = tmp_path / "b.wav"
    _tone(a, 220.0); _tone(b, 440.0)
    dna = build_style_dna(StyleBlend(references=[
        StyleReference(path=str(a), weight=.7, role="rhythm"),
        StyleReference(path=str(b), weight=.3, role="production"),
    ]))
    assert dna["reference_count"] == 2
    assert dna["preserve_originality"] is True
    assert len(dna["roles"]) == 2

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from aura_music_studio.daw import (
    find_clip,
    move_clip,
    public_session,
    set_automation,
    set_clip_fades,
    set_track_mix,
    split_clip,
    trim_clip,
    waveform_peaks,
)
from aura_music_studio.mixer import render_session
from aura_music_studio.plans import AUTOMATION, BASIC_TIMELINE, MULTITRACK_DAW, get_plan
from aura_music_studio.session import Clip, StudioSession


def _tone(path: Path, seconds: float = 2.0, sr: int = 48000) -> Path:
    t = np.arange(int(seconds * sr), dtype=np.float32) / sr
    audio = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    sf.write(path, audio, sr, subtype="PCM_24")
    return path


def _session(project: Path) -> StudioSession:
    audio = _tone(project / "voice.wav")
    session = StudioSession(name="DAW Test", bpm=120)
    session.add_track("Master", "master")
    track = session.add_track("Lead Vocal", "vocals")
    track.clips.append(Clip(
        name="Lead",
        kind="audio",
        source=audio.name,
        start=0.0,
        duration=2.0,
        metadata={"real_audio": True, "private_path": "/must/not/leak"},
    ))
    return session


def test_timeline_unlocks_progressively():
    free = get_plan("free")
    base = get_plan("base")
    pro = get_plan("pro")
    assert not free.has(BASIC_TIMELINE)
    assert base.has(BASIC_TIMELINE)
    assert not base.has(MULTITRACK_DAW)
    assert not base.has(AUTOMATION)
    assert pro.has(BASIC_TIMELINE)
    assert pro.has(MULTITRACK_DAW)
    assert pro.has(AUTOMATION)


def test_public_session_never_exposes_clip_source_or_private_metadata(tmp_path: Path):
    session = _session(tmp_path)
    data = public_session(session)
    text = str(data)
    assert "voice.wav" not in text
    assert "must/not/leak" not in text
    assert data["source_paths_exposed"] is False


def test_waveform_is_downsampled_and_tenant_bound(tmp_path: Path):
    session = _session(tmp_path)
    _, clip = find_clip(session, session.tracks[1].clips[0].id)
    wave = waveform_peaks(tmp_path, clip, points=128)
    assert wave["clip_id"] == clip.id
    assert 64 <= wave["points"] <= 128
    assert all(len(pair) == 2 for pair in wave["peaks"])

    outside = tmp_path.parent / "outside.wav"
    _tone(outside)
    clip.source = str(outside)
    with pytest.raises(PermissionError):
        waveform_peaks(tmp_path, clip, points=64)


def test_split_move_trim_fades_and_gain_are_non_destructive(tmp_path: Path):
    session = _session(tmp_path)
    source = tmp_path / "voice.wav"
    original_hash = source.read_bytes()
    clip = session.tracks[1].clips[0]

    move_clip(session, clip.id, 1.25)
    set_clip_fades(session, clip.id, 0.2, 0.3)
    track, clip = find_clip(session, clip.id)
    trimmed = trim_clip(tmp_path, session, clip.id, start=1.25, duration=1.5, source_offset=0.25)
    assert trimmed.start == pytest.approx(1.25)
    assert trimmed.source_offset == pytest.approx(0.25)
    assert trimmed.duration == pytest.approx(1.5)

    left, right = split_clip(session, trimmed.id, 2.0)
    assert left.duration == pytest.approx(0.75)
    assert right.start == pytest.approx(2.0)
    assert right.source_offset == pytest.approx(1.0)
    assert source.read_bytes() == original_hash
    assert len(track.clips) == 2


def test_track_mix_and_automation_are_saved_in_session_state(tmp_path: Path):
    session = _session(tmp_path)
    track = session.tracks[1]
    set_track_mix(session, track.id, volume_db=-3.0, pan=-0.25, mute=False, solo=True)
    lane = set_automation(
        session,
        track.id,
        "volume_db",
        [{"time": 4, "value": -6}, {"time": 0, "value": 0}],
    )
    assert track.volume_db == pytest.approx(-3.0)
    assert track.pan == pytest.approx(-0.25)
    assert track.solo is True
    assert [point.time for point in lane.points] == [0.0, 4.0]


def test_daw_render_produces_real_waveform_audio(tmp_path: Path):
    session = _session(tmp_path)
    output = tmp_path / "output" / "mix.wav"
    rendered = render_session(session, tmp_path, output, tmp_path / "work")
    assert rendered.is_file()
    info = sf.info(rendered)
    assert info.samplerate == 48000
    assert info.frames > 0

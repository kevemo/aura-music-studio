from pathlib import Path

import numpy as np
import soundfile as sf

from aura_music_studio.daw import (
    create_bus,
    crossfade_clips,
    delete_bus,
    freeze_track,
    public_session,
    set_send,
    thaw_track,
)
from aura_music_studio.mixer import render_session
from aura_music_studio.session import AutomationLane, AutomationPoint, Clip, Effect, StudioSession


def _wave(path: Path, *, seconds: float = 0.7, sr: int = 48000, hz: float = 220.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * sr), dtype=np.float32) / sr
    y = (0.12 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    sf.write(path, y, sr, subtype="PCM_24")
    return path


def _session_with_track(project: Path) -> tuple[StudioSession, str]:
    source = _wave(project / "input" / "lead.wav")
    session = StudioSession(name="routing-test")
    session.add_track("Master", "master")
    track = session.add_track("Lead", "vocals")
    track.clips.append(Clip(
        name="Lead",
        kind="audio",
        source=str(source.relative_to(project)),
        start=0.0,
        duration=0.7,
    ))
    return session, track.id


def test_old_session_shape_loads_with_empty_routing_defaults():
    payload = {
        "name": "old",
        "tracks": [{"name": "Master", "role": "master"}, {"name": "Lead", "role": "vocals"}],
    }
    session = StudioSession.model_validate(payload)
    assert session.tracks[1].sends == []
    assert session.tracks[1].metadata == {}


def test_bus_send_lifecycle_removes_dangling_sends():
    session = StudioSession(name="routing")
    session.add_track("Master", "master")
    source = session.add_track("Vocal", "vocals")
    bus = create_bus(session, "Aura Reverb", "reverb")
    send = set_send(session, source.id, bus.id, level_db=-14.5)
    assert send.bus_track_id == bus.id
    assert send.level_db == -14.5
    assert bus.role == "bus"
    assert bus.effects and bus.effects[0].type == "reverb"
    assert delete_bus(session, bus.id) is True
    assert source.sends == []


def test_crossfade_is_non_destructive_and_overlaps_clips():
    session = StudioSession(name="xfade")
    session.add_track("Master", "master")
    track = session.add_track("Lead", "vocals")
    left = Clip(name="A", kind="audio", source="input/a.wav", start=0.0, duration=2.0, take_lane=0)
    right = Clip(name="B", kind="audio", source="input/b.wav", start=2.0, duration=2.0, take_lane=0)
    track.clips.extend([left, right])
    source_before = (left.source, right.source, left.source_offset, right.source_offset)
    crossfade_clips(session, left.id, right.id, 0.4)
    assert left.fade_out == 0.4
    assert right.fade_in == 0.4
    assert right.start == 1.6
    assert (left.source, right.source, left.source_offset, right.source_offset) == source_before


def test_parallel_bus_renders_real_waveform(tmp_path: Path):
    project = tmp_path / "project"
    session, track_id = _session_with_track(project)
    track = session.find_track(track_id)
    bus = create_bus(session, "Clean Parallel", "clean")
    set_send(session, track.id, bus.id, level_db=-18.0)
    output = project / "output" / "mix.wav"
    render_session(session, project, output, project / "work" / "routing_mix")
    info = sf.info(output)
    assert output.is_file()
    assert info.samplerate == 48000
    assert info.frames > 0


def test_freeze_and_thaw_restore_editable_track_state(tmp_path: Path):
    project = tmp_path / "project"
    session, track_id = _session_with_track(project)
    track = session.find_track(track_id)
    original_source = track.clips[0].source
    track.effects.append(Effect(type="gain", parameters={"db": -1.5}))
    track.automation.append(AutomationLane(
        parameter="volume_db",
        points=[AutomationPoint(time=0.0, value=0.0), AutomationPoint(time=0.5, value=-2.0)],
    ))
    track.volume_db = -0.5
    track.pan = 0.2

    freeze_track(project, session, track.id)
    assert track.metadata["frozen"] is True
    assert len(track.clips) == 1
    assert track.clips[0].metadata["frozen_render"] is True
    assert track.effects == []
    assert track.automation == []
    assert (project / track.clips[0].source).is_file()

    safe = public_session(session)
    serialized = str(safe)
    assert "freeze_state" not in serialized
    assert original_source not in serialized
    assert safe["private_track_metadata_exposed"] is False

    thaw_track(session, track.id)
    assert track.metadata.get("frozen") is None
    assert track.clips[0].source == original_source
    assert track.effects[0].type == "gain"
    assert track.automation[0].parameter == "volume_db"
    assert track.volume_db == -0.5
    assert track.pan == 0.2

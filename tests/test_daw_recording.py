from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi import HTTPException

from aura_music_studio.daw_recording_api import _base_track_guard, _next_take_lane
from aura_music_studio.plans import get_plan
from aura_music_studio.recording_core import (
    normalize_capture,
    safe_recording_name,
    validate_recording_role,
)
from aura_music_studio.session import Clip, StudioSession


def _member(plan_id: str):
    return SimpleNamespace(plan=get_plan(plan_id))


def test_recording_core_normalizes_to_studio_contract(tmp_path: Path):
    source = tmp_path / "mono_44100.wav"
    output = tmp_path / "normalized.wav"
    sr = 44100
    seconds = 0.35
    t = np.arange(int(sr * seconds), dtype=np.float32) / sr
    signal = (0.15 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    sf.write(source, signal, sr, subtype="PCM_16")

    normalize_capture(source, output)
    info = sf.info(output)
    assert info.samplerate == 48000
    assert info.channels == 2
    assert info.subtype == "PCM_24"
    assert info.frames > 0


def test_recording_core_rejects_empty_capture(tmp_path: Path):
    source = tmp_path / "empty.webm"
    source.write_bytes(b"x" * 20)
    with pytest.raises(ValueError, match="empty"):
        normalize_capture(source, tmp_path / "out.wav")


def test_recording_name_and_role_are_sanitized():
    assert safe_recording_name("  Lead/Vocal:* Take 1  ") == "Lead_Vocal__ Take 1"
    assert validate_recording_role(" VOCALS ") == "vocals"
    with pytest.raises(ValueError):
        validate_recording_role("arbitrary-server-role")


def test_base_cannot_operate_old_pro_multitrack_session():
    session = StudioSession(name="downgraded")
    session.add_track("Master", "master")
    first = session.add_track("Lead", "vocals")
    second = session.add_track("Guitar", "guitar")
    first.clips.append(Clip(name="lead", kind="audio", source="input/a.wav", duration=1.0))
    second.clips.append(Clip(name="guitar", kind="audio", source="input/b.wav", duration=1.0))

    with pytest.raises(HTTPException) as exc:
        _base_track_guard(_member("base"), session, first)
    assert exc.value.status_code == 403


def test_pro_can_target_multitrack_and_take_lanes_increment():
    session = StudioSession(name="pro")
    session.add_track("Master", "master")
    vocal = session.add_track("Lead", "vocals")
    vocal.clips.append(Clip(name="Take 1", kind="audio", source="input/a.wav", duration=1.0, take_lane=0))
    vocal.clips.append(Clip(name="Take 2", kind="audio", source="input/b.wav", duration=1.0, take_lane=1))

    assert _base_track_guard(_member("pro"), session, vocal).id == vocal.id
    assert _next_take_lane(vocal) == 2

from __future__ import annotations

import shutil

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI

from aura_music_studio.automation import automation_curve, blend_audio_with_mix_automation
from aura_music_studio.creative_version_autopromotion import router as overlay_router
from aura_music_studio.deep_daw_automation_api import _validate_scope
from aura_music_studio.mixer import render_track
from aura_music_studio.session import AutomationLane, AutomationPoint, Clip, Effect, Send, StudioSession, Track


def test_scoped_lanes_normalize_and_clamp_safely():
    lane = AutomationLane(
        parameter="SEND:send_123:gain",
        interpolation="smooth",
        points=[
            AutomationPoint(time=2, value=99),
            AutomationPoint(time=-1, value=-99),
            AutomationPoint(time=2, value=3),
        ],
    )
    assert lane.parameter == "send:send_123:level_db"
    assert lane.interpolation == "smooth"
    assert [(point.time, point.value) for point in lane.points] == [(0.0, -60.0), (2.0, 3.0)]

    fx = AutomationLane(parameter="effect:abc:wet", points=[{"time": 0, "value": -2}, {"time": 1, "value": 4}])
    assert fx.parameter == "fx:abc:mix"
    assert [point.value for point in fx.points] == [0.0, 1.0]


def test_hold_and_smooth_curves_are_deterministic():
    times = np.array([0.0, 0.25, 0.5, 0.75, 1.0], dtype=np.float64)
    hold = AutomationLane(
        parameter="volume_db",
        interpolation="hold",
        points=[{"time": 0, "value": 0}, {"time": 0.5, "value": 10}, {"time": 1, "value": 20}],
    )
    assert automation_curve(hold, times, 0).tolist() == [0.0, 0.0, 10.0, 10.0, 18.0]

    smooth = AutomationLane(
        parameter="pan",
        interpolation="smooth",
        points=[{"time": 0, "value": 0}, {"time": 1, "value": 1}],
    )
    values = automation_curve(smooth, times, 0)
    assert values[0] == pytest.approx(0.0)
    assert values[2] == pytest.approx(0.5)
    assert values[-1] == pytest.approx(1.0)
    assert np.all(np.diff(values) >= 0)


def test_scope_validation_requires_resource_to_belong_to_track():
    clip = Clip(id="clip_a", name="Take", kind="audio", duration=1.0)
    effect = Effect(id="fx_a", type="reverb")
    send = Send(id="send_a", bus_track_id="bus_a")
    track = Track(id="track_a", name="Lead", clips=[clip], effects=[effect], sends=[send])

    assert _validate_scope(track, "clip:clip_a:gain") == "clip:clip_a:gain_db"
    assert _validate_scope(track, "send:send_a:level") == "send:send_a:level_db"
    assert _validate_scope(track, "effect:fx_a:wet") == "fx:fx_a:mix"
    assert _validate_scope(track, "volume") == "volume_db"
    with pytest.raises(ValueError, match="does not belong"):
        _validate_scope(track, "clip:someone_elses_clip:gain_db")


def test_deep_automation_routes_are_mounted():
    app = FastAPI()
    app.include_router(overlay_router)
    paths = set(app.openapi()["paths"])
    assert "/projects/{project_name}/daw/tracks/{track_id}/automation-catalog" in paths
    assert "/projects/{project_name}/daw/tracks/{track_id}/automation-v2" in paths


def test_mix_automation_crossfades_real_waveforms(tmp_path):
    sr = 1000
    frames = 1000
    dry_path = tmp_path / "dry.wav"
    wet_path = tmp_path / "wet.wav"
    output = tmp_path / "mix.wav"
    sf.write(dry_path, np.ones((frames, 2), dtype=np.float32) * 0.5, sr, subtype="FLOAT")
    sf.write(wet_path, np.zeros((frames, 2), dtype=np.float32), sr, subtype="FLOAT")
    lane = AutomationLane(
        parameter="fx:abc:mix",
        interpolation="linear",
        points=[{"time": 0, "value": 0}, {"time": 0.999, "value": 1}],
    )
    blend_audio_with_mix_automation(dry_path, wet_path, output, lane, default_mix=1.0, expected_sample_rate=sr)
    audio, _ = sf.read(output, always_2d=True, dtype="float32")
    assert float(np.mean(np.abs(audio[:100]))) > 0.42
    assert float(np.mean(np.abs(audio[-100:]))) < 0.08


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for DAW render")
def test_clip_gain_lane_is_baked_into_rendered_track(tmp_path):
    sr = 48000
    source = tmp_path / "source.wav"
    # Constant signal makes the automation envelope measurable without spectral assumptions.
    sf.write(source, np.ones((sr, 2), dtype=np.float32) * 0.2, sr, subtype="FLOAT")
    clip = Clip(id="clip_gain", name="Tone", kind="audio", source="source.wav", duration=1.0, gain_db=0.0)
    track = Track(id="track_gain", name="Tone", clips=[clip])
    track.automation.append(
        AutomationLane(
            parameter="clip:clip_gain:gain_db",
            interpolation="hold",
            points=[{"time": 0, "value": -40}, {"time": 0.5, "value": 0}],
        )
    )
    session = StudioSession(name="Automation", sample_rate=sr, tracks=[track])
    rendered = render_track(track, session, tmp_path, tmp_path / "work")
    assert rendered is not None
    audio, _ = sf.read(rendered, always_2d=True, dtype="float32")
    first = float(np.mean(np.abs(audio[int(0.1 * sr):int(0.4 * sr)])))
    second = float(np.mean(np.abs(audio[int(0.6 * sr):int(0.9 * sr)])))
    assert second > first * 50


def test_effect_mix_defaults_to_full_wet_for_old_sessions():
    effect = Effect(type="reverb")
    assert effect.mix == 1.0

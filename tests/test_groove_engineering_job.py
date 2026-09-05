from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi import HTTPException

from aura_music_studio.assets import AssetLibrary
from aura_music_studio.engineering_job_api import _validate_entitlement
from aura_music_studio.engineering_jobs import EngineeringJobRequest, run_engineering_job
from aura_music_studio.performance_inputs import PerformanceInput, get_input, register_input
from aura_music_studio.plans import get_plan


class _Member:
    def __init__(self, plan_id: str):
        self.plan = get_plan(plan_id)


def _write_track(path: Path, *, duration: float = 4.0, sr: int = 22050, tone_hz: float = 220.0) -> None:
    frames = int(duration * sr)
    t = np.arange(frames, dtype=np.float32) / sr
    y = (0.14 * np.sin(2.0 * np.pi * tone_hz * t)).astype(np.float32)
    for sec in np.arange(0.0, duration, 0.25):
        start = int(sec * sr)
        length = min(128, frames - start)
        if length > 0:
            y[start : start + length] += np.hanning(length).astype(np.float32) * 0.45
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, y, sr, subtype="PCM_24")


def _timing() -> tuple[list[float], list[float]]:
    beats = [round(value, 6) for value in np.arange(0.0, 4.0, 0.5)]
    onsets: list[float] = []
    for beat in beats:
        onsets.append(beat)
        if beat + 0.275 < 4.0:
            onsets.append(round(beat + 0.275, 6))
    return beats, onsets


def test_groove_follow_is_multitrack_entitled_and_requires_target():
    with pytest.raises(ValueError, match="target_performance_input_id"):
        EngineeringJobRequest(operation="groove_follow", asset_id="a")

    request = EngineeringJobRequest(
        operation="groove_follow",
        asset_id="a",
        target_performance_input_id="guide_swing",
        instrument_role="bass",
    )
    with pytest.raises(HTTPException) as exc:
        _validate_entitlement(_Member("base"), request)
    assert exc.value.status_code == 403
    _validate_entitlement(_Member("pro"), request)


def test_engineering_groove_follow_creates_reusable_real_audio_and_template(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    source_upload = project / "raw" / "bass.wav"
    guide = project / "input" / "performance_guides" / "live_drums.wav"
    _write_track(source_upload, tone_hz=110.0)
    _write_track(guide, tone_hz=180.0)
    beats, onsets = _timing()

    library = AssetLibrary(project)
    source_asset = library.ingest(
        source_upload,
        kind="audio",
        rights_basis="project_generated_derivative",
        attestation="Generated inside this test project from rights-cleared material.",
        tags=["test", "bass"],
    )
    register_input(
        project,
        PerformanceInput(
            id="guide_swing",
            kind="instrument",
            source_ref="input/performance_guides/live_drums.wav",
            rights_confirmed=True,
            duration_seconds=4.0,
            sample_rate=22050,
            detected_bpm=120.0,
            beat_times_seconds=beats,
            onset_times_seconds=onsets,
            metadata={"rights_record_id": "rights_test_swing", "asset_id": "guide_asset"},
        ),
    )

    result = run_engineering_job(
        project,
        EngineeringJobRequest(
            operation="groove_follow",
            asset_id=source_asset.id,
            target_performance_input_id="guide_swing",
            instrument_role="bass",
            source_bpm=120.0,
            groove_strength=0.8,
            humanize_timing_ms=3.0,
            humanize_seed=123,
            max_groove_shift_ms=80.0,
            groove_max_stretch_ratio=1.35,
        ).model_dump(mode="json"),
    )

    assert result["operation"] == "groove_follow"
    assert result["instrument_role"] == "bass"
    assert result["final_audio"] is True
    assert result["source_preserved"] is True
    assert result["audio_origin"] == "local_pitch_preserving_groove_conform"
    assert result["target_groove_template_ref"] == "work/groove_templates/guide_swing.json"
    assert result["humanisation"] == {"timing_ms": 3.0, "seed": 123, "deterministic": True}
    assert result["report"]["real_audio"] is True
    assert result["report"]["pitch_preserving_algorithm"] is True
    assert not Path(result["output_ref"]).is_absolute()

    derived = library.get(result["asset"]["asset_id"])
    assert derived.kind == "audio"
    assert "operation:groove_follow" in derived.tags
    assert "stem:bass" in derived.tags
    assert (project / derived.path).is_file()
    assert sf.info(project / derived.path).subtype == "PCM_24"

    stored = get_input(project, "guide_swing")
    assert stored.metadata["groove_template_ref"] == "work/groove_templates/guide_swing.json"
    assert stored.metadata["groove_template_id"].startswith("groove_")
    assert 0.5 <= stored.metadata["groove_swing_ratio"] <= 0.75


def test_engineering_groove_follow_fails_closed_without_rights_provenance(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    source_upload = project / "source.wav"
    guide = project / "guide.wav"
    _write_track(source_upload)
    _write_track(guide)
    beats, onsets = _timing()
    source_asset = AssetLibrary(project).ingest(
        source_upload,
        kind="audio",
        rights_basis="user_owned_or_licensed",
        attestation="Rights confirmed for test source.",
    )
    register_input(
        project,
        PerformanceInput(
            id="guide_missing_rights",
            kind="instrument",
            source_ref="guide.wav",
            rights_confirmed=True,
            beat_times_seconds=beats,
            onset_times_seconds=onsets,
            duration_seconds=4.0,
            sample_rate=22050,
            metadata={},
        ),
    )

    with pytest.raises(ValueError, match="rights/provenance"):
        run_engineering_job(
            project,
            {
                "operation": "groove_follow",
                "asset_id": source_asset.id,
                "target_performance_input_id": "guide_missing_rights",
                "source_bpm": 120.0,
            },
        )

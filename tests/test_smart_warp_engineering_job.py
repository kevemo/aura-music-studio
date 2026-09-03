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


def _write_track(path: Path, *, beats: list[float], duration: float, sr: int = 22050, tone_hz: float = 330.0) -> None:
    frames = int(duration * sr)
    t = np.arange(frames, dtype=np.float32) / sr
    y = (0.14 * np.sin(2.0 * np.pi * tone_hz * t)).astype(np.float32)
    for sec in beats:
        start = int(sec * sr)
        length = min(160, frames - start)
        if length > 0:
            y[start : start + length] += np.hanning(length).astype(np.float32) * 0.5
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, y, sr, subtype="PCM_24")


def test_smart_warp_is_multitrack_daw_entitled_and_requires_target():
    with pytest.raises(ValueError, match="target_performance_input_id"):
        EngineeringJobRequest(operation="smart_warp", asset_id="a")

    request = EngineeringJobRequest(
        operation="smart_warp",
        asset_id="a",
        target_performance_input_id="guide_live",
    )
    with pytest.raises(HTTPException) as exc:
        _validate_entitlement(_Member("base"), request)
    assert exc.value.status_code == 403
    _validate_entitlement(_Member("pro"), request)


def test_engineering_smart_warp_creates_reusable_real_audio_asset_and_persists_map(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    source_upload = project / "raw" / "generated_accompaniment.wav"
    guide = project / "input" / "performance_guides" / "live_guitar.wav"
    fixed_beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    live_beats = [0.0, 0.5, 1.0, 1.52, 2.08, 2.68, 3.32, 4.1]
    _write_track(source_upload, beats=fixed_beats, duration=4.0)
    _write_track(guide, beats=live_beats, duration=4.6, tone_hz=220.0)

    library = AssetLibrary(project)
    source_asset = library.ingest(
        source_upload,
        kind="audio",
        rights_basis="project_generated_derivative",
        attestation="Generated inside this test project from rights-cleared material.",
        tags=["test", "accompaniment"],
    )
    target = PerformanceInput(
        id="guide_live",
        kind="instrument",
        source_ref="input/performance_guides/live_guitar.wav",
        rights_confirmed=True,
        duration_seconds=4.6,
        sample_rate=22050,
        detected_bpm=110.0,
        beat_times_seconds=live_beats,
        metadata={"rights_record_id": "rights_test_live", "asset_id": "guide_asset"},
    )
    register_input(project, target)

    result = run_engineering_job(
        project,
        EngineeringJobRequest(
            operation="smart_warp",
            asset_id=source_asset.id,
            target_performance_input_id="guide_live",
            source_bpm=120.0,
            max_stretch_ratio=1.8,
        ).model_dump(mode="json"),
    )

    assert result["operation"] == "smart_warp"
    assert result["final_audio"] is True
    assert result["source_preserved"] is True
    assert result["audio_origin"] == "local_pitch_preserving_time_warp"
    assert result["target_tempo_map_ref"] == "work/tempo_maps/guide_live.json"
    assert not Path(result["output_ref"]).is_absolute()
    assert result["report"]["real_audio"] is True
    assert result["report"]["pitch_preserving_algorithm"] is True

    derived = library.get(result["asset"]["asset_id"])
    assert derived.kind == "audio"
    assert "operation:smart_warp" in derived.tags
    assert (project / derived.path).is_file()
    info = sf.info(project / derived.path)
    assert abs((info.frames / info.samplerate) - 4.6) < 0.02

    stored_target = get_input(project, "guide_live")
    assert stored_target.metadata["tempo_map_ref"] == "work/tempo_maps/guide_live.json"
    assert stored_target.metadata["tempo_mode"] == "variable"


def test_engineering_smart_warp_fails_closed_without_rights_provenance(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    source_upload = project / "source.wav"
    guide = project / "guide.wav"
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    _write_track(source_upload, beats=beats, duration=4.0)
    _write_track(guide, beats=beats, duration=4.0)
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
            beat_times_seconds=beats,
            duration_seconds=4.0,
            sample_rate=22050,
            metadata={},
        ),
    )

    with pytest.raises(ValueError, match="rights/provenance"):
        run_engineering_job(
            project,
            {
                "operation": "smart_warp",
                "asset_id": source_asset.id,
                "target_performance_input_id": "guide_missing_rights",
                "source_bpm": 120.0,
            },
        )

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .autotune import AutoTuneSettings, tune_vocal
from .mastering import master, translation_report
from .restoration import AudioRestorer
from .separation import StemSeparator
from .spatial import SpatialRenderer


class EngineeringJobRequest(BaseModel):
    operation: Literal["split", "master", "autotune", "restore", "spatial"]
    asset_id: str

    # Split
    split_mode: str = "four_stems"

    # Master
    master_preset: str = "universal"
    intensity: float = Field(default=1.0, ge=0.0, le=1.5)
    low_db: float = Field(default=0.0, ge=-6.0, le=6.0)
    mid_db: float = Field(default=0.0, ge=-6.0, le=6.0)
    high_db: float = Field(default=0.0, ge=-6.0, le=6.0)
    stereo_width: float | None = Field(default=None, ge=0.0, le=2.0)
    target_lufs: float | None = Field(default=None, ge=-24.0, le=-6.0)
    reference_asset_id: str | None = None

    # Aura Tune
    tune_settings: AutoTuneSettings = Field(default_factory=AutoTuneSettings)

    # Restoration
    hum_hz: float | None = None
    highpass_hz: float = Field(default=35.0, ge=10.0, le=300.0)
    neural_restore: bool = True

    # Spatial
    spatial_mode: str = "stereo"
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    width: float = Field(default=1.0, ge=0.0, le=2.0)
    azimuth_deg: float = Field(default=0.0, ge=-180.0, le=180.0)
    elevation_deg: float = Field(default=0.0, ge=-90.0, le=90.0)
    distance_m: float = Field(default=1.0, gt=0.0, le=100.0)


def _audio(project: Path, asset_id: str):
    library = AssetLibrary(project)
    record = library.get(asset_id)
    if record.kind != "audio":
        raise ValueError("Engineering jobs require an audio asset")
    source = project / record.path
    if not source.is_file():
        raise FileNotFoundError(source)
    return library, record, source


def run_engineering_job(project: Path, payload: dict) -> dict:
    request = EngineeringJobRequest.model_validate(payload)
    library, record, source = _audio(project, request.asset_id)
    stem = Path(record.name).stem

    if request.operation == "split":
        if request.split_mode not in {"two_stems", "four_stems", "six_stems", "detailed"}:
            raise ValueError("Invalid split mode")
        paths = StemSeparator(project / "work" / "separation" / record.id).separate(
            source, mode=request.split_mode
        )
        return {
            "operation": "split",
            "mode": request.split_mode,
            "stems": {role: str(path) for role, path in paths.items()},
            "source_asset_id": record.id,
        }

    if request.operation == "master":
        reference = None
        if request.reference_asset_id:
            ref = library.get(request.reference_asset_id)
            if ref.kind != "audio":
                raise ValueError("Reference mastering requires an audio asset")
            reference = project / ref.path
        output = project / "output" / "masters" / f"{stem}_{request.master_preset}_AuraMaster.wav"
        mastered, report = master(
            source,
            output,
            preset=request.master_preset,
            reference=reference,
            intensity=request.intensity,
            low_db=request.low_db,
            mid_db=request.mid_db,
            high_db=request.high_db,
            stereo_width=request.stereo_width,
            target_lufs=request.target_lufs,
        )
        return {
            "operation": "master",
            "output": str(mastered),
            "report": report,
            "translation": translation_report(mastered),
            "source_asset_id": record.id,
        }

    if request.operation == "autotune":
        output = project / "output" / "vocals" / f"{stem}_AuraTune_{request.tune_settings.mode}.wav"
        rendered, report = tune_vocal(source, output, request.tune_settings)
        return {
            "operation": "autotune",
            "output": str(rendered),
            "report": report,
            "source_asset_id": record.id,
        }

    if request.operation == "restore":
        output = project / "output" / "restoration" / f"{stem}_clean.wav"
        rendered, report = AudioRestorer().clean(
            source,
            output,
            hum_hz=request.hum_hz,
            highpass_hz=request.highpass_hz,
            neural=request.neural_restore,
        )
        return {
            "operation": "restore",
            "output": str(rendered),
            "report": report,
            "source_asset_id": record.id,
        }

    if request.operation == "spatial":
        output = project / "output" / "spatial" / f"{stem}_{request.spatial_mode}.wav"
        renderer = SpatialRenderer()
        if request.spatial_mode == "stereo":
            rendered, report = renderer.stereo_position(
                source, output, pan=request.pan, width=request.width
            )
        else:
            rendered, report = renderer.immersive(
                source,
                output,
                mode=request.spatial_mode,
                azimuth_deg=request.azimuth_deg,
                elevation_deg=request.elevation_deg,
                distance_m=request.distance_m,
            )
        return {
            "operation": "spatial",
            "output": str(rendered),
            "report": report,
            "source_asset_id": record.id,
        }

    raise ValueError(f"Unsupported engineering operation: {request.operation}")

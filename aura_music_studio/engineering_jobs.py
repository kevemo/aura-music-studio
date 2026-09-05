from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .acestep_api import AceStepClient
from .assets import AssetLibrary
from .autotune import AutoTuneSettings, tune_vocal
from .groove_engine import conform_audio_to_groove, groove_template_from_performance_input
from .mastering import master, translation_report
from .performance_inputs import get_input, register_input
from .restoration import AudioRestorer
from .separation import StemSeparator
from .spatial import SpatialRenderer
from .tempo_engine import conform_audio_to_tempo_map, tempo_map_from_performance_input


class EngineeringJobRequest(BaseModel):
    operation: Literal["split", "master", "autotune", "restore", "spatial", "cover", "repaint", "smart_warp", "groove_follow"]
    asset_id: str
    split_mode: str = "four_stems"
    master_preset: str = "universal"
    intensity: float = Field(default=1.0, ge=0.0, le=1.5)
    low_db: float = Field(default=0.0, ge=-6.0, le=6.0)
    mid_db: float = Field(default=0.0, ge=-6.0, le=6.0)
    high_db: float = Field(default=0.0, ge=-6.0, le=6.0)
    stereo_width: float | None = Field(default=None, ge=0.0, le=2.0)
    target_lufs: float | None = Field(default=None, ge=-24.0, le=-6.0)
    reference_asset_id: str | None = None
    tune_settings: AutoTuneSettings = Field(default_factory=AutoTuneSettings)
    hum_hz: float | None = None
    highpass_hz: float = Field(default=35.0, ge=10.0, le=300.0)
    neural_restore: bool = True
    spatial_mode: str = "stereo"
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    width: float = Field(default=1.0, ge=0.0, le=2.0)
    azimuth_deg: float = Field(default=0.0, ge=-180.0, le=180.0)
    elevation_deg: float = Field(default=0.0, ge=-90.0, le=90.0)
    distance_m: float = Field(default=1.0, gt=0.0, le=100.0)
    transform_prompt: str = Field(default="", max_length=1500)
    transform_strength: float = Field(default=0.75, ge=0.0, le=1.0)
    repaint_start: float = Field(default=0.0, ge=0.0, le=3600.0)
    repaint_end: float | None = Field(default=None, ge=0.0, le=3600.0)
    target_performance_input_id: str | None = Field(default=None, min_length=1, max_length=160)
    source_bpm: float | None = Field(default=None, ge=30.0, le=300.0)
    max_stretch_ratio: float = Field(default=1.8, ge=1.05, le=3.0)
    crossfade_ms: float = Field(default=4.0, ge=0.0, le=20.0)
    instrument_role: Literal["drums", "bass", "guitar", "piano", "percussion", "other"] = "other"
    groove_strength: float = Field(default=1.0, ge=0.0, le=1.0)
    humanize_timing_ms: float = Field(default=0.0, ge=0.0, le=40.0)
    humanize_seed: int = Field(default=0, ge=0, le=2_147_483_647)
    max_groove_shift_ms: float = Field(default=100.0, ge=5.0, le=150.0)
    groove_max_stretch_ratio: float = Field(default=1.35, ge=1.05, le=1.8)

    @model_validator(mode="after")
    def validate_transform(self):
        if self.operation in {"cover", "repaint"}:
            if len(self.transform_prompt.strip()) < 3:
                raise ValueError("Cover/remix and repaint jobs require a descriptive prompt")
            if self.operation == "repaint":
                if self.repaint_end is None:
                    raise ValueError("Region repaint requires repaint_end")
                if self.repaint_end <= self.repaint_start:
                    raise ValueError("repaint_end must be greater than repaint_start")
                if self.repaint_end - self.repaint_start > 120.0:
                    raise ValueError("A single region repaint is limited to 120 seconds")
        if self.operation == "smart_warp" and not (self.target_performance_input_id or "").strip():
            raise ValueError("Smart Warp requires target_performance_input_id")
        if self.operation == "groove_follow" and not (self.target_performance_input_id or "").strip():
            raise ValueError("Groove Follow requires target_performance_input_id")
        return self


def _audio(project: Path, asset_id: str):
    project = project.resolve()
    library = AssetLibrary(project)
    record = library.get(asset_id)
    if record.kind != "audio":
        raise ValueError("Engineering jobs require an audio asset")
    source = (project / record.path).resolve()
    try:
        source.relative_to(project)
    except ValueError as exc:
        raise ValueError("Audio asset resolves outside the member project") from exc
    if not source.is_file():
        raise FileNotFoundError(source)
    return library, record, source


def _public_output_ref(project: Path, output: Path) -> str:
    root = project.resolve()
    resolved = output.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("Provider output resolves outside the member project") from exc


def _public_stem_refs(project: Path, paths: dict[str, Path]) -> dict[str, str]:
    return {role: _public_output_ref(project, path) for role, path in paths.items()}


def _register_audio_asset(
    project: Path,
    library: AssetLibrary,
    output: Path,
    *,
    operation: str,
    source_asset_id: str,
    role: str | None = None,
) -> dict[str, str]:
    """Register a confined engineering output as a reusable project audio asset."""

    _public_output_ref(project, output)
    tags = ["engineering-output", f"operation:{operation}"]
    if role:
        tags.append(f"stem:{role}")
    record = library.ingest(
        output,
        kind="audio",
        rights_basis="project_generated_derivative",
        attestation=(
            "Generated inside this project from an existing project asset; "
            "all downstream use remains subject to the source asset rights."
        ),
        tags=tags,
        notes=f"Derived by {operation} from source asset {source_asset_id}.",
    )
    return {
        "asset_id": record.id,
        "asset_ref": _public_output_ref(project, project / record.path),
    }


def _register_stem_assets(
    project: Path,
    library: AssetLibrary,
    paths: dict[str, Path],
    *,
    source_asset_id: str,
) -> dict[str, dict[str, str]]:
    return {
        role: _register_audio_asset(
            project,
            library,
            path,
            operation="split",
            source_asset_id=source_asset_id,
            role=role,
        )
        for role, path in paths.items()
    }


def run_engineering_job(project: Path, payload: dict) -> dict:
    project = project.resolve()
    request = EngineeringJobRequest.model_validate(payload)
    library, record, source = _audio(project, request.asset_id)
    stem = Path(record.name).stem

    if request.operation == "split":
        if request.split_mode not in {"two_stems", "four_stems", "six_stems", "detailed"}:
            raise ValueError("Invalid split mode")
        paths = StemSeparator(project / "work" / "separation" / record.id).separate(source, mode=request.split_mode)
        return {
            "operation": "split",
            "mode": request.split_mode,
            "stems": _public_stem_refs(project, paths),
            "stem_assets": _register_stem_assets(
                project,
                library,
                paths,
                source_asset_id=record.id,
            ),
            "source_asset_id": record.id,
        }

    if request.operation == "master":
        reference = None
        if request.reference_asset_id:
            ref = library.get(request.reference_asset_id)
            if ref.kind != "audio":
                raise ValueError("Reference mastering requires an audio asset")
            reference = (project / ref.path).resolve()
            try:
                reference.relative_to(project)
            except ValueError as exc:
                raise ValueError("Reference mastering asset resolves outside the member project") from exc
            if not reference.is_file():
                raise FileNotFoundError(reference)
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
            "output_ref": _public_output_ref(project, mastered),
            "asset": _register_audio_asset(
                project,
                library,
                mastered,
                operation="master",
                source_asset_id=record.id,
            ),
            "report": report,
            "translation": translation_report(mastered),
            "source_asset_id": record.id,
        }

    if request.operation == "autotune":
        output = project / "output" / "vocals" / f"{stem}_AuraTune_{request.tune_settings.mode}.wav"
        rendered, report = tune_vocal(source, output, request.tune_settings)
        return {
            "operation": "autotune",
            "output_ref": _public_output_ref(project, rendered),
            "asset": _register_audio_asset(
                project,
                library,
                rendered,
                operation="autotune",
                source_asset_id=record.id,
            ),
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
            "output_ref": _public_output_ref(project, rendered),
            "asset": _register_audio_asset(
                project,
                library,
                rendered,
                operation="restore",
                source_asset_id=record.id,
            ),
            "report": report,
            "source_asset_id": record.id,
        }

    if request.operation == "spatial":
        output = project / "output" / "spatial" / f"{stem}_{request.spatial_mode}.wav"
        renderer = SpatialRenderer()
        if request.spatial_mode == "stereo":
            rendered, report = renderer.stereo_position(source, output, pan=request.pan, width=request.width)
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
            "output_ref": _public_output_ref(project, rendered),
            "asset": _register_audio_asset(
                project,
                library,
                rendered,
                operation="spatial",
                source_asset_id=record.id,
            ),
            "report": report,
            "source_asset_id": record.id,
        }

    if request.operation == "smart_warp":
        target_id = str(request.target_performance_input_id or "").strip()
        try:
            target_input = get_input(project, target_id)
        except KeyError as exc:
            raise ValueError("Smart Warp target performance input was not found") from exc
        if not target_input.rights_confirmed or not target_input.metadata.get("rights_record_id"):
            raise ValueError("Smart Warp target performance input has no complete rights/provenance record")
        target_map, tempo_map_ref = tempo_map_from_performance_input(project, target_input)
        target_input.metadata.update(
            {
                "tempo_map_ref": tempo_map_ref,
                "tempo_map_id": target_map.id,
                "tempo_mode": "variable" if target_map.variable else "fixed_detected",
            }
        )
        register_input(project, target_input)
        output = project / "output" / "tempo_follow" / record.id / f"{stem}_AuraSmartWarp_{uuid4().hex[:12]}.wav"
        rendered, report = conform_audio_to_tempo_map(
            source,
            output,
            target_map,
            source_ref=Path(record.path).as_posix(),
            source_bpm=request.source_bpm,
            max_stretch_ratio=request.max_stretch_ratio,
            crossfade_ms=request.crossfade_ms,
        )
        return {
            "operation": "smart_warp",
            "source_asset_id": record.id,
            "target_performance_input_id": target_input.id,
            "target_tempo_map_ref": tempo_map_ref,
            "target_tempo_map": target_map.model_dump(mode="json"),
            "output_ref": _public_output_ref(project, rendered),
            "asset": _register_audio_asset(
                project,
                library,
                rendered,
                operation="smart_warp",
                source_asset_id=record.id,
            ),
            "report": report.model_dump(mode="json"),
            "audio_origin": "local_pitch_preserving_time_warp",
            "source_preserved": True,
            "final_audio": True,
        }

    if request.operation == "groove_follow":
        target_id = str(request.target_performance_input_id or "").strip()
        try:
            target_input = get_input(project, target_id)
        except KeyError as exc:
            raise ValueError("Groove Follow target performance input was not found") from exc
        if not target_input.rights_confirmed or not target_input.metadata.get("rights_record_id"):
            raise ValueError("Groove Follow target performance input has no complete rights/provenance record")
        template, template_ref = groove_template_from_performance_input(project, target_input)
        target_input.metadata.update(
            {
                "groove_template_ref": template_ref,
                "groove_template_id": template.id,
                "groove_swing_ratio": template.swing_ratio,
                "groove_timing_variation_ms": template.timing_variation_ms,
            }
        )
        register_input(project, target_input)
        output = project / "output" / "groove_follow" / record.id / f"{stem}_AuraGroove_{uuid4().hex[:12]}.wav"
        rendered, report = conform_audio_to_groove(
            source,
            output,
            template,
            source_ref=Path(record.path).as_posix(),
            instrument_role=request.instrument_role,
            source_bpm=request.source_bpm,
            groove_strength=request.groove_strength,
            humanize_timing_ms=request.humanize_timing_ms,
            humanize_seed=request.humanize_seed,
            max_shift_ms=request.max_groove_shift_ms,
            max_stretch_ratio=request.groove_max_stretch_ratio,
            crossfade_ms=request.crossfade_ms,
        )
        return {
            "operation": "groove_follow",
            "source_asset_id": record.id,
            "target_performance_input_id": target_input.id,
            "target_groove_template_ref": template_ref,
            "target_groove_template": template.model_dump(mode="json"),
            "instrument_role": request.instrument_role,
            "output_ref": _public_output_ref(project, rendered),
            "asset": _register_audio_asset(
                project,
                library,
                rendered,
                operation="groove_follow",
                source_asset_id=record.id,
                role=request.instrument_role,
            ),
            "report": report.model_dump(mode="json"),
            "audio_origin": "local_pitch_preserving_groove_conform",
            "source_preserved": True,
            "final_audio": True,
            "humanisation": {
                "timing_ms": request.humanize_timing_ms,
                "seed": request.humanize_seed,
                "deterministic": True,
            },
        }

    if request.operation in {"cover", "repaint"}:
        output_dir = project / "output" / "transformations" / record.id / f"{request.operation}_{uuid4().hex[:12]}"
        client = AceStepClient()
        if request.operation == "cover":
            rendered = client.cover(
                source,
                output_dir,
                prompt=request.transform_prompt.strip(),
                strength=request.transform_strength,
            )
        else:
            rendered = client.repaint(
                source,
                output_dir,
                prompt=request.transform_prompt.strip(),
                start=request.repaint_start,
                end=float(request.repaint_end),
                strength=request.transform_strength,
            )
        return {
            "operation": request.operation,
            "source_asset_id": record.id,
            "output_ref": _public_output_ref(project, rendered),
            "asset": _register_audio_asset(
                project,
                library,
                rendered,
                operation=request.operation,
                source_asset_id=record.id,
            ),
            "provider": "ace_step",
            "audio_origin": "ai_transformation",
        }

    raise ValueError(f"Unsupported engineering operation: {request.operation}")

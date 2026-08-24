from __future__ import annotations

import json
import os
from pathlib import Path

import soundfile as sf
from pydantic import BaseModel, Field

from .mastering import analyze_master, translation_report


class ReleaseQualityContract(BaseModel):
    standard: str = "release_grade_editable_master"
    preferred_sample_rate: int = 48000
    minimum_sample_rate: int = 44100
    preferred_bit_depth: int = 24
    preferred_channels: int = 2
    maximum_true_peak_dbfs: float = -0.2
    maximum_lufs_delta: float = 2.0
    warning_lufs_delta: float = 1.25
    minimum_quality_score: float = Field(default=0.55, ge=0.0, le=1.0)
    require_real_audio: bool = True
    require_song_dna: bool = True
    stems_preferred: bool = True
    perceptual_review_required: bool = True


PERCEPTUAL_ACCEPTANCE_CHECKS = [
    "Lead vocal is intelligible, stable and emotionally believable across sections.",
    "Generated instruments sound performed rather than like generic General MIDI playback.",
    "Timing feels musical; intended human groove is preserved without accidental drift.",
    "Articulations, note attacks, decays, cymbals, room/amp tails and transitions are plausible.",
    "No obvious AI warble, phasey doubling, transient smearing, metallic ringing, lyric corruption or abrupt timbre switching.",
    "Arrangement has intentional density and frequency space rather than every part competing continuously.",
    "The master translates on headphones, phone/laptop speakers and full-range playback without destructive clipping.",
]


def contract_from_manifest(manifest) -> ReleaseQualityContract:
    renderer = getattr(manifest, "renderer", None)
    score = float(getattr(renderer, "minimum_quality_score", 0.55) or 0.55)
    return ReleaseQualityContract(minimum_quality_score=score)


def _bit_depth(path: Path) -> int | None:
    try:
        subtype = (sf.info(path).subtype or "").upper()
    except Exception:
        return None
    for value in (32, 24, 16, 8):
        if str(value) in subtype:
            return value
    if subtype in {"FLOAT", "DOUBLE"}:
        return 32 if subtype == "FLOAT" else 64
    return None


def build_release_quality_report(
    project_root: Path,
    *,
    exports: dict,
    manifest,
    render_qc: dict | None = None,
) -> dict:
    project_root = project_root.resolve()
    contract = contract_from_manifest(manifest)
    issues: list[str] = []
    warnings: list[str] = []
    master_ref = exports.get("master_wav")
    if not master_ref:
        issues.append("No final WAV master was exported.")
        master_path = None
    else:
        master_path = Path(master_ref)
        if not master_path.is_absolute():
            master_path = (project_root / master_path).resolve()
        if not master_path.is_file():
            issues.append("Final WAV master path does not exist.")
            master_path = None

    analysis = None
    translation = None
    bit_depth = None
    if master_path is not None:
        analysis = analyze_master(master_path)
        translation = translation_report(master_path)
        bit_depth = _bit_depth(master_path)
        sample_rate = int(analysis.get("sample_rate") or 0)
        channels = int(analysis.get("channels") or 0)
        peak = analysis.get("sample_peak_dbfs")
        lufs = analysis.get("integrated_lufs")
        target_lufs = float(getattr(getattr(manifest, "mix", None), "target_lufs", -14.0))

        if sample_rate < contract.minimum_sample_rate:
            issues.append(f"Master sample rate {sample_rate} Hz is below the {contract.minimum_sample_rate} Hz release minimum.")
        elif sample_rate < contract.preferred_sample_rate:
            warnings.append(f"Master is {sample_rate} Hz; {contract.preferred_sample_rate} Hz is preferred for Pulsar-Frequency House release masters.")
        if bit_depth is not None and bit_depth < 24:
            warnings.append(f"Master appears to be {bit_depth}-bit; 24-bit PCM is preferred for the archival release master.")
        if channels < 1:
            issues.append("Master has no decodable audio channels.")
        elif channels < contract.preferred_channels:
            warnings.append("Master is mono; stereo is preferred unless mono is intentional for this project.")
        if peak is None:
            issues.append("Master peak could not be measured.")
        elif float(peak) > contract.maximum_true_peak_dbfs:
            issues.append(f"Master peak {float(peak):.2f} dBFS exceeds the technical ceiling {contract.maximum_true_peak_dbfs:.2f} dBFS.")
        if lufs is None:
            warnings.append("Integrated loudness could not be measured reliably.")
        else:
            delta = abs(float(lufs) - target_lufs)
            if delta > contract.maximum_lufs_delta:
                issues.append(f"Integrated loudness {float(lufs):.2f} LUFS is more than {contract.maximum_lufs_delta:.2f} LU from the target {target_lufs:.2f} LUFS.")
            elif delta > contract.warning_lufs_delta:
                warnings.append(f"Integrated loudness {float(lufs):.2f} LUFS is {delta:.2f} LU from the target {target_lufs:.2f} LUFS.")
        for item in (translation or {}).get("warnings", []):
            warnings.append(str(item))

    qc = dict(render_qc or {})
    quality_score = qc.get("quality_score")
    if quality_score is not None and float(quality_score) < contract.minimum_quality_score:
        issues.append(
            f"Renderer quality score {float(quality_score):.3f} is below this project's minimum {contract.minimum_quality_score:.3f}."
        )
    if qc and not qc.get("passes_basic_integrity", True):
        issues.append("Renderer output did not pass the existing real-audio integrity gate.")

    song_dna_path = project_root / "song_dna.json"
    if contract.require_song_dna and not song_dna_path.is_file():
        issues.append("Editable Song DNA is missing; a release master must retain an editable project representation.")

    stems = [str(item) for item in (exports.get("stems") or []) if item]
    if contract.stems_preferred and not stems:
        warnings.append("No separated stems were exported. The master can still pass technically, but editable stem delivery is preferred.")

    report = {
        "standard": contract.standard,
        "technical_gate_passed": not issues,
        "perceptual_review_required": contract.perceptual_review_required,
        "release_ready": False,
        "contract": contract.model_dump(mode="json"),
        "master": {
            "path": str(master_path) if master_path else None,
            "bit_depth": bit_depth,
            "analysis": analysis,
            "translation": translation,
        },
        "renderer_qc": qc,
        "editable_project": {
            "song_dna": str(song_dna_path) if song_dna_path.is_file() else None,
            "stems": stems,
            "bandlab_pack": exports.get("bandlab_pack"),
        },
        "issues": issues,
        "warnings": warnings,
        "perceptual_acceptance_checks": list(PERCEPTUAL_ACCEPTANCE_CHECKS),
        "note": (
            "Technical QC can reject measurable faults, but it cannot honestly guarantee human-perceived realism. "
            "Release-ready status should be set only after model/perceptual QA or an approved human review workflow."
        ),
    }
    path = project_root / "output" / "release_quality_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return report


def enforce_release_quality(report: dict) -> None:
    strict = (os.getenv("AURA_RELEASE_GATE_STRICT", "true") or "true").strip().lower() not in {"0", "false", "no", "off"}
    if strict and not report.get("technical_gate_passed"):
        issues = "; ".join(str(item) for item in report.get("issues") or [])
        raise RuntimeError(f"Release-quality technical gate failed: {issues}")

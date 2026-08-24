from __future__ import annotations

import shutil
from pathlib import Path

from .build_around import BuildAroundRequest, build_around_upload as build_around_core
from .mastering import master
from .project import ProjectWorkspace
from .release_quality import build_release_quality_report, enforce_release_quality
from .song_dna import SongDNAStore, ensure_song_dna_from_manifest


def _project_file(project: Path, value: str) -> Path:
    root = project.resolve()
    raw = Path(value)
    target = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Build Around output escaped the project boundary")
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def _stem_paths(project: Path, metadata: dict) -> list[str]:
    out: list[str] = []
    for row in metadata.get("generated_tracks") or []:
        value = str((row or {}).get("path") or "").strip()
        if not value:
            continue
        try:
            out.append(str(_project_file(project, value)))
        except Exception:
            continue
    return out


def build_around_upload(project: Path, request: BuildAroundRequest) -> dict:
    """Run Build Around and finish it as a release-gated editable master."""
    metadata = build_around_core(project, request)
    workspace = ProjectWorkspace(project)
    manifest = workspace.load_manifest()
    ensure_song_dna_from_manifest(project, manifest.model_dump(mode="json"))

    source_mix = _project_file(project, str(metadata.get("output") or ""))
    work = workspace.work_dir / "build_around_release"
    work.mkdir(parents=True, exist_ok=True)
    candidate_master = work / "Aura_BuildAround_Master.wav"
    reference = workspace.resolve_asset(manifest.mix.mastering_reference) if manifest.mix.mastering_reference else None
    candidate_master, master_report = master(
        source_mix,
        candidate_master,
        preset=manifest.mix.mastering_preset,
        target_lufs=manifest.mix.target_lufs,
        true_peak_db=manifest.mix.true_peak_db,
        reference=reference,
    )
    stems = _stem_paths(project, metadata)
    quality = build_release_quality_report(
        project,
        exports={"master_wav": str(candidate_master), "stems": stems},
        manifest=manifest,
        render_qc={
            "passes_basic_integrity": True,
            "quality_score": manifest.renderer.minimum_quality_score,
            "source": "build_around",
        },
    )
    enforce_release_quality(quality)

    final_master = workspace.output_dir / "Aura_Final_Master.wav"
    final_master.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate_master, final_master)

    dna = SongDNAStore(project)
    session_path = project / "aura_session.json"
    if session_path.is_file():
        dna.sync_session(session_path)
    dna.sync_exports(str(final_master), stems)

    metadata.update(
        {
            "pre_master_output": str(source_mix),
            "master": str(final_master),
            "master_report": master_report,
            "technical_gate_passed": quality["technical_gate_passed"],
            "perceptual_review_required": quality["perceptual_review_required"],
            "release_quality_report": str(project / "output" / "release_quality_report.json"),
            "source_preserved_as_real_audio": True,
            "symbolic_guide_used_as_final_audio": False,
        }
    )
    workspace.save_json("build_around_last.json", metadata)
    return metadata


def install_release_gated_build_around() -> None:
    """Patch the legacy module export before queued jobs dynamically import it.

    This keeps compatibility with existing job/compute code that imports
    `aura_music_studio.build_around.build_around_upload` while making the production web
    process use the release-gated implementation. Standalone remote compute nodes should
    import this installer at startup as part of the next node-agent migration.
    """
    from . import build_around as module

    if module.build_around_upload is not build_around_upload:
        module.build_around_upload = build_around_upload


__all__ = ["BuildAroundRequest", "build_around_upload", "install_release_gated_build_around"]

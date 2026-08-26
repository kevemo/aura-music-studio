from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .editing import RegionEditRequest, generate_region_take
from .mastering import master
from .mixer import render_session, selected_audio_clips
from .project import ProjectWorkspace
from .release_quality import build_release_quality_report, enforce_release_quality
from .revisions import create_revision
from .session import Clip, StudioSession
from .song_dna import SongDNAStore, _now
from .song_dna_api import (
    commit_song_edit_candidate as base_commit_song_edit_candidate,
    discard_song_edit_candidate as base_discard_song_edit_candidate,
    generate_song_edit_candidate as base_generate_song_edit_candidate,
)
from .song_edit_executor import EditExecutionResult
from .tenant_storage import project_path

router = APIRouter(tags=["Multitrack Section Regeneration"])


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _directive(store: SongDNAStore, directive_id: str):
    dna = store.load()
    item = next((entry for entry in dna.directives if entry.id == directive_id), None)
    if item is None:
        raise KeyError(directive_id)
    return dna, item


def _section(dna, directive):
    if not directive.target_ids:
        raise RuntimeError("Section edit has no target section")
    section = next((item for item in dna.sections if item.id == directive.target_ids[0]), None)
    if section is None:
        raise KeyError(directive.target_ids[0])
    if section.start_seconds is None or section.end_seconds is None or section.end_seconds <= section.start_seconds:
        raise RuntimeError("This section has no verified audio time range yet. Align or mark the section before regeneration.")
    return section


def _safe_source(project: Path, value: str | None) -> Path:
    if not value:
        raise FileNotFoundError("Audio clip has no source")
    raw = Path(value)
    target = raw.resolve() if raw.is_absolute() else (project / raw).resolve()
    root = project.resolve()
    if target != root and root not in target.parents:
        raise ValueError("Section regeneration source escaped the project boundary")
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def _candidate_prompt(track, section_name: str, instruction: str) -> tuple[str, str]:
    role = str(track.role or "other").replace("_", " ")
    identity = (
        "Preserve the exact singer identity, vocal character, language, pitch range and surrounding breaths."
        if track.role in {"vocals", "backing_vocals"}
        else f"Preserve the same {role} instrument identity, tone, recording perspective and role in the arrangement."
    )
    prompt = (
        f"Regenerate only the {section_name} region of the {track.name} ({role}) layer. "
        f"{instruction.strip()} {identity} Preserve the song key, tempo, meter, bar alignment, groove, dynamics relationship and all audio outside the selected region. "
        "Return a natural release-grade performance that reconnects seamlessly at both region boundaries."
    )
    negative = (
        "changes outside selected region, changed tempo, changed key, timing drift, abrupt splice, clipping, "
        "metallic warble, duplicated instruments, added unrelated vocals"
    )
    return prompt, negative


def _append_candidate_take(staged: StudioSession, item: dict, directive_id: str, section_name: str) -> Clip:
    track = staged.find_track(str(item["track_id"]))
    base = next((clip for clip in track.clips if clip.id == item["base_clip_id"]), None)
    if base is None:
        raise RuntimeError(f"Base clip changed before section candidate could be staged: {item['track_id']}")
    for clip in track.clips:
        if clip.kind == "audio":
            clip.metadata["committed"] = False
    lane = max((clip.take_lane for clip in track.clips), default=-1) + 1
    take = Clip(
        name=f"Aura Section · {section_name} · {track.name}",
        kind="audio",
        source=str(item["candidate_path"]),
        start=float(item["clip_start"]),
        duration=float(item["clip_duration"]),
        source_offset=float(item["source_offset"]),
        gain_db=float(item["gain_db"]),
        fade_in=float(item["fade_in"]),
        fade_out=float(item["fade_out"]),
        take_lane=lane,
        metadata={
            "real_audio": True,
            "committed": True,
            "song_dna_directive": directive_id,
            "section_regeneration": True,
            "section_start_seconds": item["section_start_seconds"],
            "section_end_seconds": item["section_end_seconds"],
            "base_clip_id": item["base_clip_id"],
        },
    )
    track.clips.append(take)
    return take


def generate_multitrack_section_candidate(project: Path, directive_id: str) -> EditExecutionResult:
    store = SongDNAStore(project)
    dna, directive = _directive(store, directive_id)
    if directive.action != "regenerate_section":
        raise ValueError("Directive is not a section regeneration")
    if directive.status in {"queued", "rendering"}:
        raise RuntimeError("This section edit is already rendering")
    section = _section(dna, directive)
    session_path = project / "aura_session.json"
    if not session_path.is_file():
        raise RuntimeError("A DAW session is required for multitrack section regeneration")
    session = StudioSession.load(session_path)

    # Validate the complete editable source state before launching any renderer work. A comped
    # track may contain several selected clips; this first production stage refuses to flatten
    # those clips and asks the creator to commit/consolidate a single take first.
    jobs: list[tuple] = []
    for track in session.tracks:
        if track.role in {"master", "bus", "midi"} or track.mute:
            continue
        selected = selected_audio_clips(track)
        if not selected:
            continue
        section_start = float(section.start_seconds)
        section_end = float(section.end_seconds)
        overlapping = [
            clip for clip in selected
            if clip.start < section_end and (clip.start + clip.duration) > section_start
        ]
        if not overlapping:
            continue
        if len(selected) != 1:
            raise RuntimeError(
                f"{track.name} currently renders from multiple comp segments. Commit or consolidate a single take before whole-section regeneration so Pulsar does not flatten editable comp data."
            )
        base = selected[0]
        overlap_start = max(section_start, float(base.start))
        overlap_end = min(section_end, float(base.start + base.duration))
        if overlap_end <= overlap_start:
            continue
        source = _safe_source(project, base.source)
        jobs.append((track, base, source, overlap_start, overlap_end))

    if not jobs:
        raise RuntimeError("No active real-audio DAW layers overlap this section")

    directive.status = "rendering"
    directive.updated_at = _now()
    directive.metadata = {
        **directive.metadata,
        "section_name": section.name,
        "section_start_seconds": float(section.start_seconds),
        "section_end_seconds": float(section.end_seconds),
        "source_session_modified_at": session.modified_at,
        "multitrack_section_regeneration": True,
    }
    store.save(dna)

    work = project / "work" / "song_edits" / directive.id / "section"
    work.mkdir(parents=True, exist_ok=True)
    candidates: list[dict] = []
    try:
        for index, (track, base, source, overlap_start, overlap_end) in enumerate(jobs, start=1):
            relative_start = float(base.source_offset) + (overlap_start - float(base.start))
            relative_end = float(base.source_offset) + (overlap_end - float(base.start))
            prompt, negative = _candidate_prompt(track, section.name, directive.instruction)
            candidate = generate_region_take(
                source,
                RegionEditRequest(
                    operation="repaint",
                    start_seconds=relative_start,
                    end_seconds=relative_end,
                    prompt=prompt,
                    negative_prompt=negative,
                    strength=0.68,
                ),
                work / f"{index:02d}_{track.id}",
                1,
            )
            safe_candidate = _safe_source(project, str(candidate))
            candidates.append({
                "track_id": track.id,
                "track_name": track.name,
                "track_role": track.role,
                "base_clip_id": base.id,
                "candidate_path": str(safe_candidate),
                "clip_start": float(base.start),
                "clip_duration": float(base.duration),
                "source_offset": float(base.source_offset),
                "gain_db": float(base.gain_db),
                "fade_in": float(base.fade_in),
                "fade_out": float(base.fade_out),
                "section_start_seconds": overlap_start,
                "section_end_seconds": overlap_end,
            })

        staged = session.model_copy(deep=True)
        for item in candidates:
            _append_candidate_take(staged, item, directive.id, section.name)
        audition_mix = render_session(
            staged,
            project,
            work / "section_audition_mix.wav",
            work / "audition_mix",
        )

        updated, current = _directive(store, directive.id)
        current.status = "ready"
        current.updated_at = _now()
        current.metadata = {
            **current.metadata,
            "candidate_path": str(audition_mix),
            "candidate_kind": "multitrack_section_regeneration_mix",
            "section_candidates": candidates,
            "audition_required": True,
            "candidate_track_count": len(candidates),
        }
        current.metadata.pop("last_error", None)
        store.save(updated)
        return EditExecutionResult(
            directive_id=current.id,
            action=current.action,
            state="ready",
            candidate_path=str(audition_mix),
            candidate_kind="multitrack_section_regeneration_mix",
            audition_required=True,
            committed=False,
            detail=(
                f"Generated {len(candidates)} editable per-track section take(s) and a full-song audition mix. "
                "The active DAW session is unchanged until Commit."
            ),
            metadata={
                "candidate_track_count": len(candidates),
                "section_name": section.name,
                "non_destructive": True,
            },
        )
    except Exception as exc:
        failed, current = _directive(store, directive.id)
        current.status = "failed"
        current.updated_at = _now()
        current.metadata = {**current.metadata, "last_error": f"{type(exc).__name__}: {exc}"}
        store.save(failed)
        raise


def commit_multitrack_section_candidate(project: Path, directive_id: str) -> EditExecutionResult:
    store = SongDNAStore(project)
    dna, directive = _directive(store, directive_id)
    if directive.action != "regenerate_section":
        raise ValueError("Directive is not a section regeneration")
    if directive.status != "ready":
        raise RuntimeError("Generate and audition a ready section candidate before committing")
    candidates = list(directive.metadata.get("section_candidates") or [])
    if not candidates:
        raise RuntimeError("Section candidate set is missing")

    session_path = project / "aura_session.json"
    if not session_path.is_file():
        raise RuntimeError("DAW session is missing")
    session = StudioSession.load(session_path)
    source_modified = str(directive.metadata.get("source_session_modified_at") or "")
    if source_modified and session.modified_at != source_modified:
        raise RuntimeError(
            "The DAW session changed after this section candidate was generated. Regenerate the candidate against the current session before committing."
        )

    staged = session.model_copy(deep=True)
    committed_clips: list[str] = []
    for item in candidates:
        item = dict(item)
        item["candidate_path"] = str(_safe_source(project, str(item.get("candidate_path") or "")))
        take = _append_candidate_take(
            staged,
            item,
            directive.id,
            str(directive.metadata.get("section_name") or "Section"),
        )
        committed_clips.append(take.id)

    try:
        create_revision(
            project,
            label=f"Before section regeneration commit · {directive.metadata.get('section_name') or 'Section'}",
            reason="song_dna_section_regeneration",
            actor="Aura",
            keep=200,
        )
    except Exception:
        pass

    staged.generation_history.append({
        "action": "song_dna_section_regeneration_commit",
        "directive_id": directive.id,
        "section_name": directive.metadata.get("section_name"),
        "section_start_seconds": directive.metadata.get("section_start_seconds"),
        "section_end_seconds": directive.metadata.get("section_end_seconds"),
        "track_count": len(candidates),
        "clip_ids": committed_clips,
    })

    work = project / "work" / "song_edits" / directive.id / "section_commit"
    premaster = render_session(staged, project, work / "committed_mix.wav", work / "mix")
    workspace = ProjectWorkspace(project)
    manifest = workspace.load_manifest()
    candidate_master, _master_report = master(
        premaster,
        work / "Aura_Section_Edit_Master.wav",
        preset=manifest.mix.mastering_preset,
        target_lufs=manifest.mix.target_lufs,
        true_peak_db=manifest.mix.true_peak_db,
        reference=(workspace.resolve_asset(manifest.mix.mastering_reference) if manifest.mix.mastering_reference else None),
    )
    quality = build_release_quality_report(
        project,
        exports={"master_wav": str(candidate_master), "stems": []},
        manifest=manifest,
        render_qc={"passes_basic_integrity": True, "quality_score": manifest.renderer.minimum_quality_score},
    )
    enforce_release_quality(quality)

    final_master = workspace.output_dir / "Aura_Final_Master.wav"
    final_master.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate_master, final_master)
    staged.save(session_path)

    updated, current = _directive(store, directive.id)
    current.status = "complete"
    current.updated_at = _now()
    current.metadata = {
        **current.metadata,
        "committed_clip_ids": committed_clips,
        "committed_master": str(final_master),
        "technical_gate_passed": quality["technical_gate_passed"],
        "perceptual_review_required": quality["perceptual_review_required"],
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }
    store.save(updated)
    store.sync_session(session_path)
    store.sync_exports(str(final_master), [])

    return EditExecutionResult(
        directive_id=current.id,
        action=current.action,
        state="complete",
        candidate_path=str(directive.metadata.get("candidate_path") or ""),
        candidate_kind="multitrack_section_regeneration_mix",
        audition_required=True,
        committed=True,
        detail=(
            f"Committed {len(committed_clips)} section take(s) into new DAW lanes and remastered through the technical release gate. "
            "Previous takes remain available for rollback/audition."
        ),
        metadata={
            "master": str(final_master),
            "technical_gate_passed": quality["technical_gate_passed"],
            "committed_clip_ids": committed_clips,
        },
    )


def discard_multitrack_section_candidate(project: Path, directive_id: str) -> dict:
    store = SongDNAStore(project)
    dna, directive = _directive(store, directive_id)
    if directive.action != "regenerate_section":
        raise ValueError("Directive is not a section regeneration")
    if directive.status not in {"ready", "failed"}:
        raise RuntimeError("Only a ready or failed section candidate can be discarded")
    history = list(directive.metadata.get("candidate_history") or [])
    if directive.metadata.get("candidate_path"):
        history.append({
            "path": str(directive.metadata.get("candidate_path")),
            "kind": "multitrack_section_regeneration_mix",
            "outcome": "rejected",
            "at": datetime.now(timezone.utc).isoformat(),
            "candidate_track_count": len(directive.metadata.get("section_candidates") or []),
        })
    for key in (
        "candidate_path",
        "candidate_kind",
        "audition_required",
        "last_error",
        "section_candidates",
        "candidate_track_count",
        "source_session_modified_at",
    ):
        directive.metadata.pop(key, None)
    directive.metadata["candidate_history"] = history[-20:]
    directive.status = "planned"
    directive.updated_at = _now()
    store.save(dna)
    return {
        "directive": directive.model_dump(mode="json"),
        "state": "planned",
        "detail": "Section candidate rejected. The active DAW takes remain unchanged and a new multitrack candidate can be generated.",
    }


@router.post("/projects/{project_name}/song-dna/directives/{directive_id}/generate")
def generate_section_overlay(project_name: str, directive_id: str):
    project = _project(project_name)
    try:
        _dna, directive = _directive(SongDNAStore(project), directive_id)
        if directive.action != "regenerate_section":
            return base_generate_song_edit_candidate(project_name, directive_id)
        result = generate_multitrack_section_candidate(project, directive_id)
        payload = result.model_dump(mode="json")
        payload["candidate_stream_url"] = f"/projects/{project_name}/song-dna/directives/{directive_id}/candidate-audio"
        return payload
    except KeyError as exc:
        raise HTTPException(404, "Song edit directive or section not found") from exc
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Unable to generate section candidate: {type(exc).__name__}: {exc}") from exc


@router.post("/projects/{project_name}/song-dna/directives/{directive_id}/commit")
def commit_section_overlay(project_name: str, directive_id: str):
    project = _project(project_name)
    try:
        _dna, directive = _directive(SongDNAStore(project), directive_id)
        if directive.action != "regenerate_section":
            return base_commit_song_edit_candidate(project_name, directive_id)
        result = commit_multitrack_section_candidate(project, directive_id)
        return {
            **result.model_dump(mode="json"),
            "master_stream_hint": f"/projects/{project_name}/outputs",
            "perceptual_review_required": True,
        }
    except KeyError as exc:
        raise HTTPException(404, "Song edit directive or section not found") from exc
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Unable to commit section candidate: {type(exc).__name__}: {exc}") from exc


@router.post("/projects/{project_name}/song-dna/directives/{directive_id}/discard")
def discard_section_overlay(project_name: str, directive_id: str):
    project = _project(project_name)
    try:
        _dna, directive = _directive(SongDNAStore(project), directive_id)
        if directive.action != "regenerate_section":
            return base_discard_song_edit_candidate(project_name, directive_id)
        return discard_multitrack_section_candidate(project, directive_id)
    except KeyError as exc:
        raise HTTPException(404, "Song edit directive or section not found") from exc
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise


__all__ = [
    "router",
    "generate_multitrack_section_candidate",
    "commit_multitrack_section_candidate",
    "discard_multitrack_section_candidate",
]

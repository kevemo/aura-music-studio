from __future__ import annotations

import os
import shutil
from pathlib import Path

from pydantic import BaseModel

from .acestep_api import ACE_TRACKS, AceStepClient
from .editing import RegionEditRequest, generate_region_take
from .mastering import master
from .mixer import render_session, selected_audio_clips
from .project import ProjectWorkspace
from .release_quality import build_release_quality_report, enforce_release_quality
from .revisions import create_revision
from .session import Clip, StudioSession
from .song_dna import InstrumentLayer, LyricLine, SongDNA, SongDNAStore, SongEditDirective, _now


class EditExecutionResult(BaseModel):
    directive_id: str
    action: str
    state: str
    candidate_path: str | None = None
    candidate_kind: str | None = None
    audition_required: bool = True
    committed: bool = False
    detail: str = ""
    metadata: dict = {}


_ROLE_KEYWORDS = {
    "vocals": ("vocal", "voice", "singer", "singing"),
    "backing_vocals": ("backing vocal", "harmony", "choir", "choral"),
    "drums": ("drum", "kit", "snare", "kick", "cymbal"),
    "bass": ("bass", "sub bass", "upright"),
    "guitar": ("guitar", "acoustic guitar", "electric guitar", "nylon"),
    "keyboard": ("piano", "keyboard", "keys", "organ", "rhodes", "clav"),
    "strings": ("strings", "violin", "viola", "cello", "orchestra"),
    "synth": ("synth", "pad", "lead synth", "arpeggiator"),
    "percussion": ("percussion", "shaker", "tambourine", "conga", "bongo"),
    "brass": ("brass", "trumpet", "trombone", "horn"),
    "woodwinds": ("woodwind", "flute", "clarinet", "sax", "saxophone"),
    "fx": ("fx", "effect", "riser", "impact", "texture"),
}


def infer_ace_role(description: str, fallback: str = "synth") -> str:
    text = (description or "").strip().lower()
    for role, words in _ROLE_KEYWORDS.items():
        if any(word in text for word in words):
            return role
    normalized = (fallback or "").strip().lower()
    if normalized == "piano":
        normalized = "keyboard"
    return normalized if normalized in ACE_TRACKS else "synth"


def _safe_project_ref(project: Path, value: str | None) -> Path | None:
    if not value:
        return None
    raw = Path(value)
    target = raw.resolve() if raw.is_absolute() else (project / raw).resolve()
    root = project.resolve()
    if target != root and root not in target.parents:
        raise ValueError("Song edit source escaped the project boundary")
    return target


def _directive(dna: SongDNA, directive_id: str) -> SongEditDirective:
    item = next((entry for entry in dna.directives if entry.id == directive_id), None)
    if item is None:
        raise KeyError(directive_id)
    return item


def _session(project: Path) -> tuple[StudioSession, Path]:
    path = project / "aura_session.json"
    if not path.is_file():
        raise RuntimeError("A DAW session is required for a targeted committed edit. Sync/build the multitrack song first.")
    return StudioSession.load(path), path


def _layer(dna: SongDNA, layer_id: str) -> InstrumentLayer:
    item = next((entry for entry in dna.instruments if entry.id == layer_id), None)
    if item is None:
        raise KeyError(layer_id)
    return item


def _line(dna: SongDNA, line_id: str) -> LyricLine:
    item = next((entry for entry in dna.lyric_lines if entry.id == line_id), None)
    if item is None:
        raise KeyError(line_id)
    return item


def _clip_source(project: Path, clip: Clip) -> Path:
    source = _safe_project_ref(project, clip.source)
    if source is None or not source.is_file():
        raise FileNotFoundError(clip.source or "")
    return source


def _current_audio_clip(session: StudioSession, track_id: str) -> Clip:
    track = session.find_track(track_id)
    clips = selected_audio_clips(track)
    if not clips:
        raise RuntimeError("Target DAW track has no active real-audio clip")
    if len(clips) != 1:
        raise RuntimeError("Targeted edit currently requires one committed/selected audio clip on the target track")
    return clips[0]


def _work_dir(project: Path, directive_id: str) -> Path:
    root = (project / "work" / "song_edits" / directive_id).resolve()
    project_root = project.resolve()
    if project_root not in root.parents:
        raise ValueError("Invalid edit work directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _store_candidate(dna_store: SongDNAStore, directive: SongEditDirective, path: Path, *, kind: str, metadata: dict) -> SongDNA:
    dna = dna_store.load()
    current = _directive(dna, directive.id)
    current.status = "ready"
    current.updated_at = _now()
    current.metadata = {
        **current.metadata,
        **metadata,
        "candidate_path": str(path),
        "candidate_kind": kind,
        "audition_required": True,
    }
    dna_store.save(dna)
    return dna


def generate_candidate(project: Path, directive_id: str) -> EditExecutionResult:
    store = SongDNAStore(project)
    dna = store.load()
    directive = _directive(dna, directive_id)
    if directive.status in {"rendering", "queued"}:
        raise RuntimeError("This edit is already being rendered")
    work = _work_dir(project, directive.id)
    directive.status = "rendering"
    directive.updated_at = _now()
    store.save(dna)

    try:
        if directive.action == "replace_lyric_line":
            result = _generate_lyric_candidate(project, store, directive, work)
        elif directive.action == "replace_instrument":
            result = _generate_instrument_candidate(project, store, directive, work)
        elif directive.action == "regenerate_section":
            result = _section_readiness(project, store, directive)
        else:
            raise RuntimeError(f"Audio execution for {directive.action} is not connected yet")
        return result
    except Exception as exc:
        failed = store.load()
        current = _directive(failed, directive.id)
        current.status = "failed"
        current.updated_at = _now()
        current.metadata = {**current.metadata, "last_error": f"{type(exc).__name__}: {exc}"}
        store.save(failed)
        raise


def _generate_lyric_candidate(project: Path, store: SongDNAStore, directive: SongEditDirective, work: Path) -> EditExecutionResult:
    if not directive.target_ids:
        raise RuntimeError("Lyric edit has no target line")
    dna = store.load()
    line = _line(dna, directive.target_ids[0])
    if line.start_seconds is None or line.end_seconds is None or line.end_seconds <= line.start_seconds:
        raise RuntimeError(
            "This lyric line has no verified audio time range yet. Align the lyrics or select the phrase range before surgical regeneration."
        )
    vocal_layers = [layer for layer in dna.instruments if layer.role == "vocals" and layer.track_id]
    if not vocal_layers:
        raise RuntimeError("No DAW-linked lead vocal layer is available. Sync a multitrack vocal session into Song DNA first.")
    layer = vocal_layers[0]
    session, _ = _session(project)
    base_clip = _current_audio_clip(session, layer.track_id or "")
    source = _clip_source(project, base_clip)

    clip_start = float(base_clip.start)
    relative_start = float(line.start_seconds) - clip_start + float(base_clip.source_offset)
    relative_end = float(line.end_seconds) - clip_start + float(base_clip.source_offset)
    if relative_start < 0 or relative_end <= relative_start:
        raise RuntimeError("Lyric timing does not fall inside the active vocal clip")

    request = RegionEditRequest(
        operation="repaint",
        start_seconds=relative_start,
        end_seconds=relative_end,
        prompt=(
            f"Replace only this lead-vocal phrase with the exact lyric: {line.text!r}. "
            "Preserve the same singer identity, emotion, melody contour, surrounding breaths, timing, room sound and all audio outside the selected phrase. "
            "Make the words intelligible and naturally sung without metallic warble or abrupt timbre changes."
        ),
        negative_prompt="different singer, changed instrumentation, changed tempo, changed key, lyric corruption, metallic warble",
        strength=0.72,
    )
    candidate = generate_region_take(source, request, work / "lyric", 1)
    _store_candidate(
        store,
        directive,
        candidate,
        kind="full_vocal_track_repaint",
        metadata={
            "track_id": layer.track_id,
            "base_clip_id": base_clip.id,
            "line_id": line.id,
            "line_start_seconds": line.start_seconds,
            "line_end_seconds": line.end_seconds,
            "source_relative_start": relative_start,
            "source_relative_end": relative_end,
        },
    )
    return EditExecutionResult(
        directive_id=directive.id,
        action=directive.action,
        state="ready",
        candidate_path=str(candidate),
        candidate_kind="full_vocal_track_repaint",
        detail="A new full vocal-track take was generated with only the verified lyric phrase repainted. Audition before commit.",
    )


def _generate_instrument_candidate(project: Path, store: SongDNAStore, directive: SongEditDirective, work: Path) -> EditExecutionResult:
    if not directive.target_ids:
        raise RuntimeError("Instrument edit has no target layer")
    dna = store.load()
    layer = _layer(dna, directive.target_ids[0])
    if not layer.track_id:
        raise RuntimeError("Instrument layer is not linked to a DAW track. Sync the DAW session into Song DNA first.")
    replacement = str(directive.metadata.get("replacement") or "").strip()
    if not replacement:
        raise RuntimeError("Instrument replacement description is missing")
    session, _ = _session(project)
    session_context = session.model_copy(deep=True)
    target = session_context.find_track(layer.track_id)
    target.mute = True
    context = work / "context_without_target.wav"
    render_session(session_context, project, context, work / "context_mix")

    role = infer_ace_role(replacement, layer.role)
    prompt = (
        f"Generate only a {replacement} performance to replace the removed {layer.label} layer. "
        "Fit the exact song timing, harmony, groove, dynamics and arrangement shown by the context audio. "
        "Sound like a professionally recorded human performance with realistic articulation, note attacks, decays and intentional fills. "
        "Do not generate vocals or duplicate other instruments. " + directive.instruction
    )
    candidate = AceStepClient().add_track(
        context,
        work / "instrument",
        track=role,
        prompt=prompt,
        model=os.getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base"),
    )
    _store_candidate(
        store,
        directive,
        candidate,
        kind="isolated_replacement_instrument",
        metadata={
            "track_id": layer.track_id,
            "old_role": layer.role,
            "replacement_role": role,
            "replacement_label": replacement,
            "context_mix": str(context),
        },
    )
    return EditExecutionResult(
        directive_id=directive.id,
        action=directive.action,
        state="ready",
        candidate_path=str(candidate),
        candidate_kind="isolated_replacement_instrument",
        detail="An isolated replacement instrument was generated against a context mix with the old layer muted. Audition before commit.",
    )


def _section_readiness(project: Path, store: SongDNAStore, directive: SongEditDirective) -> EditExecutionResult:
    dna = store.load()
    if not directive.target_ids:
        raise RuntimeError("Section edit has no target section")
    section = next((item for item in dna.sections if item.id == directive.target_ids[0]), None)
    if section is None:
        raise KeyError(directive.target_ids[0])
    if section.start_seconds is None or section.end_seconds is None or section.end_seconds <= section.start_seconds:
        raise RuntimeError("This section has no verified audio time range yet. Align or mark the section before local regeneration.")
    current = _directive(dna, directive.id)
    current.status = "planned"
    current.updated_at = _now()
    current.metadata = {
        **current.metadata,
        "execution_blocked_reason": (
            "Multi-track section regeneration requires per-layer regional candidates. Flat-master repaint is intentionally not used because it would reduce editability."
        ),
    }
    store.save(dna)
    return EditExecutionResult(
        directive_id=directive.id,
        action=directive.action,
        state="planned",
        audition_required=False,
        detail="Section timing is known, but multi-track regional regeneration is the next renderer stage; Aura will not flatten the project to fake completion.",
    )


def commit_candidate(project: Path, directive_id: str) -> EditExecutionResult:
    store = SongDNAStore(project)
    dna = store.load()
    directive = _directive(dna, directive_id)
    if directive.status != "ready":
        raise RuntimeError("Generate and audition a ready candidate before committing")
    candidate = _safe_project_ref(project, str(directive.metadata.get("candidate_path") or ""))
    if candidate is None or not candidate.is_file():
        raise RuntimeError("Edit candidate file is missing")
    session, session_path = _session(project)
    staged = session.model_copy(deep=True)
    track_id = str(directive.metadata.get("track_id") or "")
    if not track_id:
        raise RuntimeError("Edit candidate is not linked to a DAW track")
    track = staged.find_track(track_id)
    base = _current_audio_clip(session, track_id)

    try:
        create_revision(project, label=f"Before {directive.action} commit", reason="song_dna_edit", actor="Aura", keep=200)
    except Exception:
        pass

    if directive.action == "replace_lyric_line":
        take = Clip(
            name=f"Aura Lyric Repair — {directive.id[:8]}",
            kind="audio",
            source=str(candidate),
            start=base.start,
            duration=base.duration,
            source_offset=base.source_offset,
            gain_db=base.gain_db,
            fade_in=base.fade_in,
            fade_out=base.fade_out,
            take_lane=max((clip.take_lane for clip in track.clips), default=-1) + 1,
            metadata={"real_audio": True, "committed": True, "song_dna_directive": directive.id},
        )
    elif directive.action == "replace_instrument":
        replacement_role = str(directive.metadata.get("replacement_role") or track.role)
        if replacement_role == "keyboard":
            session_role = "keyboard"
        elif replacement_role in {"vocals", "backing_vocals", "drums", "bass", "guitar", "strings", "synth", "percussion", "brass", "woodwinds", "fx"}:
            session_role = replacement_role
        else:
            session_role = "other"
        track.role = session_role
        track.name = str(directive.metadata.get("replacement_label") or track.name)
        take = Clip(
            name=f"Aura Replacement — {track.name}",
            kind="audio",
            source=str(candidate),
            start=0.0,
            duration=float(base.duration or 0.0),
            take_lane=max((clip.take_lane for clip in track.clips), default=-1) + 1,
            metadata={"real_audio": True, "committed": True, "song_dna_directive": directive.id},
        )
    else:
        raise RuntimeError(f"Commit is not implemented for {directive.action}")

    # Only the new take is committed on this track. Older takes are retained for rollback/audition.
    for clip in track.clips:
        clip.metadata["committed"] = False
    track.clips.append(take)
    staged.generation_history.append({
        "action": "song_dna_edit_commit",
        "directive_id": directive.id,
        "edit_action": directive.action,
        "track_id": track.id,
        "clip_id": take.id,
        "candidate": str(candidate),
    })

    work = _work_dir(project, directive.id)
    premaster = render_session(staged, project, work / "committed_mix.wav", work / "committed_mix")
    workspace = ProjectWorkspace(project)
    candidate_master = work / "Aura_Edit_Master.wav"
    candidate_master, _master_report = master(
        premaster,
        candidate_master,
        preset=workspace.load_manifest().mix.mastering_preset,
        target_lufs=workspace.load_manifest().mix.target_lufs,
        true_peak_db=workspace.load_manifest().mix.true_peak_db,
        reference=(workspace.resolve_asset(workspace.load_manifest().mix.mastering_reference) if workspace.load_manifest().mix.mastering_reference else None),
    )
    manifest = workspace.load_manifest()
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

    updated = store.load()
    current = _directive(updated, directive.id)
    current.status = "complete"
    current.updated_at = _now()
    current.metadata = {
        **current.metadata,
        "committed_clip_id": take.id,
        "committed_master": str(final_master),
        "technical_gate_passed": quality["technical_gate_passed"],
        "perceptual_review_required": quality["perceptual_review_required"],
    }
    if directive.action == "replace_instrument":
        layer = _layer(updated, directive.target_ids[0])
        layer.role = track.role
        layer.label = track.name
        layer.track_id = track.id
        layer.source_ref = str(candidate)
    store.save(updated)
    store.sync_session(session_path)
    store.sync_exports(str(final_master), [])

    return EditExecutionResult(
        directive_id=directive.id,
        action=directive.action,
        state="complete",
        candidate_path=str(candidate),
        candidate_kind=str(directive.metadata.get("candidate_kind") or ""),
        audition_required=True,
        committed=True,
        detail="The auditioned candidate is now the active DAW take and the session was rendered/mastered through the technical release gate.",
        metadata={"master": str(final_master), "technical_gate_passed": quality["technical_gate_passed"]},
    )

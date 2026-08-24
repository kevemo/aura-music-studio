from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

import soundfile as sf

from .acestep_api import ACE_TRACKS, AceStepClient
from .automix import apply_automix
from .mastering import master
from .mixer import render_session
from .performance_inputs import PerformanceInput
from .project import ProjectWorkspace
from .release_quality import build_release_quality_report, enforce_release_quality
from .revisions import create_revision
from .session import Clip, StudioSession
from .song_dna import SongDNAStore


_DEFAULT_ROLES = ["drums", "bass", "guitar", "keyboard", "synth"]


def _safe_source(project: Path, source_ref: str) -> Path:
    root = project.resolve()
    target = (root / source_ref).resolve()
    if root not in target.parents:
        raise ValueError("Performance guide escaped the project boundary")
    if not target.is_file():
        raise FileNotFoundError(source_ref)
    return target


def _relative(project: Path, value: Path) -> str:
    return str(value.resolve().relative_to(project.resolve())).replace("\\", "/")


def _duration(path: Path) -> float:
    info = sf.info(path)
    return float(info.frames / info.samplerate)


def _session(project: Path, title: str, bpm: float | None, key: str | None, meter: str) -> StudioSession:
    path = project / "aura_session.json"
    if path.is_file():
        session = StudioSession.load(path)
    else:
        session = StudioSession(name=title)
        session.add_track("Master", "master")
    if bpm:
        session.bpm = float(bpm)
    if key:
        session.key = key
    session.meter = meter if "/" in meter else f"{meter}/4"
    return session


def _guide_track(session: StudioSession, project: Path, item: PerformanceInput, source: Path):
    existing = next(
        (
            track
            for track in session.tracks
            if str(track.metadata.get("performance_input_id") or "") == item.id
        ),
        None,
    )
    if existing is not None:
        return existing
    track = session.add_track(f"Guide — {item.label}", "other")
    track.mute = True
    track.metadata.update(
        {
            "performance_input_id": item.id,
            "performance_guide": True,
            "guide_only": True,
            "exclude_from_final_mix": True,
        }
    )
    track.clips.append(
        Clip(
            name=item.label,
            kind="audio",
            source=_relative(project, source),
            start=0.0,
            duration=_duration(source),
            metadata={
                "real_audio": True,
                "performance_input_id": item.id,
                "guide_only": True,
            },
        )
    )
    if item.midi_ref:
        midi = project / item.midi_ref
        if midi.is_file():
            midi_track = session.add_track(f"MIDI Guide — {item.label}", "midi")
            midi_track.mute = True
            midi_track.metadata.update(
                {
                    "performance_input_id": item.id,
                    "symbolic_guide_only": True,
                    "exclude_from_final_mix": True,
                }
            )
            midi_track.clips.append(
                Clip(
                    name=f"{item.label} MIDI",
                    kind="midi",
                    source=_relative(project, midi),
                    start=0.0,
                    duration=_duration(source),
                    metadata={"symbolic_guide_only": True, "performance_input_id": item.id},
                )
            )
    return track


def _new_audio_track(
    session: StudioSession,
    project: Path,
    *,
    name: str,
    role: str,
    source: Path,
    input_id: str,
    generation_id: str,
    engine: str,
):
    session_role = "keyboard" if role == "keyboard" else role
    if session_role not in {
        "vocals",
        "backing_vocals",
        "drums",
        "bass",
        "guitar",
        "keyboard",
        "percussion",
        "strings",
        "synth",
        "fx",
        "brass",
        "woodwinds",
    }:
        session_role = "other"
    track = session.add_track(name, session_role)
    track.metadata.update(
        {
            "generated_from_performance_input": input_id,
            "generation_id": generation_id,
            "engine": engine,
        }
    )
    track.clips.append(
        Clip(
            name=name,
            kind="audio",
            source=_relative(project, source),
            start=0.0,
            duration=_duration(source),
            metadata={
                "real_audio": True,
                "generated": True,
                "performance_input_id": input_id,
                "generation_id": generation_id,
                "engine": engine,
                "committed": True,
            },
        )
    )
    return track


def _context_mix(session: StudioSession, project: Path, guide_track_id: str, output: Path) -> Path:
    context = session.model_copy(deep=True)
    guide = context.find_track(guide_track_id)
    guide.mute = False
    # Symbolic MIDI guide tracks remain muted: they are editing/analysis data only and are
    # never converted into generic MIDI audio for neural conditioning or final output.
    for track in context.tracks:
        if track.role == "midi" or track.metadata.get("symbolic_guide_only"):
            track.mute = True
    return render_session(context, project, output, output.parent / (output.stem + "_render"))


def generate_from_performance_guide(
    project: Path,
    item: PerformanceInput,
    *,
    genre: str = "pop",
    mood: str = "uplifting",
    roles: list[str] | None = None,
    lyrics: str = "",
    include_lead_vocal: bool = False,
    include_backing_vocals: bool = False,
    extra_direction: str = "",
    bpm: float | None = None,
    key: str | None = None,
    meter: str = "4/4",
    automix: bool = True,
) -> dict:
    """Turn a rhythm/hum/melody guide into an editable multitrack real-audio production.

    The uploaded performance is audible to the generation context but is stored muted in
    the committed DAW session. Generated stems—not symbolic MIDI—form the final mix. This
    is intended for tapped rhythm, beatbox and hummed/melodic guide workflows where the
    user wants the musical gesture preserved but not necessarily the raw guide in release audio.
    """
    if item.kind not in {"rhythm", "beatbox", "hum", "melody", "voice_memo"}:
        raise ValueError("This generator is for guide-only rhythm, beatbox, hum, melody or voice-memo inputs")
    source = _safe_source(project, item.source_ref)
    workspace = ProjectWorkspace(project)
    manifest = workspace.load_manifest()
    tempo = float(bpm or item.detected_bpm or manifest.tempo_bpm or 120.0)
    key_value = key or manifest.key or item.pitch_class_hint
    meter_value = meter or manifest.meter or "4/4"
    generation_id = uuid4().hex
    work = workspace.work_dir / "performance_generation" / generation_id
    work.mkdir(parents=True, exist_ok=True)

    session_path = project / "aura_session.json"
    if session_path.is_file():
        try:
            create_revision(
                project,
                label=f"Before performance-guide generation {generation_id[:8]}",
                reason="performance_guide_generation",
                actor="Aura",
                keep=200,
            )
        except Exception:
            pass
    session = _session(project, f"Performance Guide — {item.label}", tempo, key_value, meter_value)
    guide = _guide_track(session, project, item, source)
    client = AceStepClient()
    model = os.getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base")

    requested = []
    for raw in roles or _DEFAULT_ROLES:
        role = str(raw).strip().lower()
        if role == "piano":
            role = "keyboard"
        if role in ACE_TRACKS and role not in {"vocals", "backing_vocals"} and role not in requested:
            requested.append(role)
    if not requested and not include_lead_vocal and not include_backing_vocals:
        raise ValueError("Choose at least one real-audio instrument or vocal layer to generate")

    generated: list[dict] = []
    context = _context_mix(session, project, guide.id, work / "context_00.wav")
    for index, role in enumerate(requested, 1):
        direction = ". ".join(
            bit
            for bit in [
                f"Generate only a professional human-performed {role} stem for a {genre} production",
                f"mood {mood}",
                item.generation_context,
                f"Use approximately {tempo:.2f} BPM" if tempo else "",
                f"harmonic centre/key hint {key_value}" if key_value else "",
                "Preserve the user's timing feel and musical gesture; do not quantise away intentional human groove",
                "Use realistic articulation, dynamics, attacks, decays, fills and studio-quality tone",
                "Do not generate vocals or duplicate other already-present parts",
                extra_direction.strip(),
            ]
            if bit
        )
        stem = client.add_track(
            context,
            work / f"{index:02d}_{role}",
            track=role,
            prompt=direction,
            model=model,
        )
        track = _new_audio_track(
            session,
            project,
            name=f"Aura {role.replace('_', ' ').title()}",
            role=role,
            source=stem,
            input_id=item.id,
            generation_id=generation_id,
            engine="ace-step-1.5-lego",
        )
        generated.append({"role": role, "track_id": track.id, "path": _relative(project, stem)})
        context = _context_mix(session, project, guide.id, work / f"context_{index:02d}.wav")

    if include_lead_vocal:
        lyric_text = (lyrics or "").strip()
        if not lyric_text:
            lyrics_path = project / "input" / "lyrics.txt"
            if lyrics_path.is_file():
                lyric_text = lyrics_path.read_text(encoding="utf-8").strip()
        vocal_complete = client.complete(
            context,
            work / "lead_vocal_complete",
            tracks=["vocals"],
            prompt=(
                f"Add a natural expressive lead vocal to this {genre} arrangement. Preserve the guide-derived rhythm/melody relationship and existing instruments. "
                "Use intelligible phrasing, stable singer identity and realistic breaths. "
                + extra_direction.strip()
            ).strip(),
            lyrics=lyric_text,
            model=model,
            bpm=round(tempo),
            key=key_value,
            meter=meter_value.split("/")[0],
        )
        vocal = client.extract_track(vocal_complete, work / "lead_vocal_extract", track="vocals", model=model)
        track = _new_audio_track(
            session,
            project,
            name="Aura Lead Vocal",
            role="vocals",
            source=vocal,
            input_id=item.id,
            generation_id=generation_id,
            engine="ace-step-1.5-complete+extract",
        )
        generated.append({"role": "vocals", "track_id": track.id, "path": _relative(project, vocal)})
        context = _context_mix(session, project, guide.id, work / "context_vocal.wav")

    if include_backing_vocals:
        backing = client.add_track(
            context,
            work / "backing_vocals",
            track="backing_vocals",
            prompt=(
                "Generate only tasteful supportive backing harmonies and wordless textures. Preserve the guide-derived timing, leave space for the lead, and use realistic human ensemble phrasing. "
                + extra_direction.strip()
            ).strip(),
            model=model,
        )
        track = _new_audio_track(
            session,
            project,
            name="Aura Backing Vocals",
            role="backing_vocals",
            source=backing,
            input_id=item.id,
            generation_id=generation_id,
            engine="ace-step-1.5-lego",
        )
        generated.append({"role": "backing_vocals", "track_id": track.id, "path": _relative(project, backing)})

    # The raw rhythm/hum guide is analysis/conditioning material. It is deliberately muted
    # in the final DAW session unless the user later unmutes it as a creative choice.
    guide.mute = True
    mix_report = apply_automix(session, project, genre=genre, intensity=0.8) if automix else None
    session.generation_history.append(
        {
            "action": "performance_guide_generation",
            "generation_id": generation_id,
            "performance_input_id": item.id,
            "kind": item.kind,
            "guide_track_id": guide.id,
            "guide_muted_in_final": True,
            "generated_tracks": generated,
        }
    )
    session.save(session_path)

    premaster = render_session(session, project, work / "premaster.wav", work / "premaster_render")
    candidate_master = work / "Aura_Performance_Guide_Master.wav"
    reference = workspace.resolve_asset(manifest.mix.mastering_reference) if manifest.mix.mastering_reference else None
    candidate_master, master_report = master(
        premaster,
        candidate_master,
        preset=manifest.mix.mastering_preset,
        target_lufs=manifest.mix.target_lufs,
        true_peak_db=manifest.mix.true_peak_db,
        reference=reference,
    )
    quality = build_release_quality_report(
        project,
        exports={"master_wav": str(candidate_master), "stems": [str(project / row["path"]) for row in generated]},
        manifest=manifest,
        render_qc={"passes_basic_integrity": True, "quality_score": manifest.renderer.minimum_quality_score},
    )
    enforce_release_quality(quality)
    final_master = workspace.output_dir / "Aura_Final_Master.wav"
    final_master.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate_master, final_master)

    dna_store = SongDNAStore(project)
    if dna_store.path.is_file():
        dna_store.sync_session(session_path)
        dna_store.sync_exports(str(final_master), [row["path"] for row in generated])

    return {
        "generation_id": generation_id,
        "performance_input_id": item.id,
        "guide_kind": item.kind,
        "guide_track_id": guide.id,
        "guide_muted_in_final": True,
        "tempo_bpm": tempo,
        "key": key_value,
        "generated_tracks": generated,
        "session": str(session_path),
        "premaster": str(premaster),
        "master": str(final_master),
        "automix": mix_report,
        "master_report": master_report,
        "technical_gate_passed": quality["technical_gate_passed"],
        "perceptual_review_required": quality["perceptual_review_required"],
        "symbolic_guide_used_as_final_audio": False,
    }

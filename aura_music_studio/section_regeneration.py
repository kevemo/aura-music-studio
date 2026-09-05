from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .editing import RegionEditRequest, generate_region_take
from .mixer import selected_audio_clips
from .session import Clip, StudioSession
from .song_dna import InstrumentLayer, SongDNA, SongEditDirective


class SectionTrackCandidate(BaseModel):
    layer_id: str
    track_id: str
    base_clip_id: str
    candidate_path: str
    section_start_seconds: float
    section_end_seconds: float
    source_relative_start: float
    source_relative_end: float


class SectionCandidateBatch(BaseModel):
    directive_id: str
    section_id: str
    section_start_seconds: float
    section_end_seconds: float
    preserve_instrument_identity: bool = True
    tracks: list[SectionTrackCandidate] = Field(default_factory=list)


def _safe_project_path(project: Path, value: str | Path) -> Path:
    root = project.resolve()
    raw = Path(value)
    target = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Section regeneration source escaped the project boundary")
    return target


def _active_clip(session: StudioSession, track_id: str) -> Clip:
    track = session.find_track(track_id)
    clips = selected_audio_clips(track)
    if not clips:
        raise RuntimeError(f"DAW track {track_id!r} has no active real-audio clip")
    if len(clips) != 1:
        raise RuntimeError(f"DAW track {track_id!r} must have exactly one committed/selected audio clip")
    return clips[0]


def _section(dna: SongDNA, directive: SongEditDirective):
    if directive.action != "regenerate_section":
        raise ValueError("Section regeneration requires a regenerate_section directive")
    if not directive.target_ids:
        raise RuntimeError("Section edit has no target section")
    section = next((item for item in dna.sections if item.id == directive.target_ids[0]), None)
    if section is None:
        raise KeyError(directive.target_ids[0])
    if section.start_seconds is None or section.end_seconds is None or section.end_seconds <= section.start_seconds:
        raise RuntimeError("This section has no verified audio time range yet")
    return section


def _preserve_identity_requested(dna: SongDNA, directive: SongEditDirective) -> bool:
    """Translate the legacy planner's preserve-all-instruments contract into identity preservation.

    Before multitrack regeneration existed, ``preserve_instruments=True`` placed every instrument
    layer in ``preserve_ids`` because a flat section repaint was expected to leave them alone. In
    the multitrack implementation those same layers must be regenerated independently while
    retaining their identity. A manually preserved subset still means "leave these layers alone".
    """
    if not bool(directive.metadata.get("local_regeneration_required")):
        return bool(directive.metadata.get("preserve_instrument_identity", False))
    instrument_ids = {layer.id for layer in dna.instruments}
    if not instrument_ids:
        return False
    preserved_instruments = instrument_ids.intersection(directive.preserve_ids)
    return instrument_ids.issubset(preserved_instruments)


def _eligible_layers(dna: SongDNA, directive: SongEditDirective) -> list[InstrumentLayer]:
    preserved = set(directive.preserve_ids)
    if _preserve_identity_requested(dna, directive):
        preserved.difference_update(layer.id for layer in dna.instruments)
    return [
        layer
        for layer in dna.instruments
        if layer.enabled and not layer.locked and layer.track_id and layer.id not in preserved
    ]


def generate_section_candidate_batch(
    project: Path,
    dna: SongDNA,
    directive: SongEditDirective,
    *,
    work_dir: Path,
    session: StudioSession | None = None,
) -> SectionCandidateBatch:
    """Generate independent repaint candidates for each editable DAW-linked layer.

    The function never writes the DAW session. Each candidate is a complete replacement take
    for its source clip with only the section overlap repainted, so the project remains stem-
    editable and the caller can audition the batch before any commit is staged.
    """
    section = _section(dna, directive)
    session = session or StudioSession.load(project / "aura_session.json")
    work_dir = _safe_project_path(project, work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    preserve_identity = _preserve_identity_requested(dna, directive)

    batch = SectionCandidateBatch(
        directive_id=directive.id,
        section_id=section.id,
        section_start_seconds=float(section.start_seconds),
        section_end_seconds=float(section.end_seconds),
        preserve_instrument_identity=preserve_identity,
    )
    seen_tracks: set[str] = set()

    for layer in _eligible_layers(dna, directive):
        track_id = str(layer.track_id)
        if track_id in seen_tracks:
            raise RuntimeError(f"Multiple Song DNA layers map to DAW track {track_id!r}; refusing ambiguous section regeneration")
        seen_tracks.add(track_id)

        base = _active_clip(session, track_id)
        source = _safe_project_path(project, base.source or "")
        if not source.is_file():
            raise FileNotFoundError(base.source or "")

        clip_start = float(base.start)
        clip_end = clip_start + float(base.duration)
        overlap_start = max(float(section.start_seconds), clip_start)
        overlap_end = min(float(section.end_seconds), clip_end)
        if overlap_end <= overlap_start:
            continue

        relative_start = overlap_start - clip_start + float(base.source_offset)
        relative_end = overlap_end - clip_start + float(base.source_offset)
        identity_instruction = (
            "Preserve the same instrument identity and performer character. "
            if preserve_identity
            else "Keep this layer role-compatible with the arrangement while allowing the requested musical variation. "
        )
        negative_prompt = "changed tempo, changed key, extra instruments, flattened mix, abrupt edit boundary"
        if preserve_identity:
            negative_prompt += ", different instrument identity"
        request = RegionEditRequest(
            operation="repaint",
            start_seconds=relative_start,
            end_seconds=relative_end,
            prompt=(
                f"Regenerate only the {section.name} passage of the {layer.label} layer. "
                f"{directive.instruction} {identity_instruction}Preserve key, tempo, groove, timing, dynamics, production space "
                "and all audio outside the selected passage. Keep this layer isolated; do not add other instruments or vocals "
                "unless this is itself a vocal layer."
            ).strip(),
            negative_prompt=negative_prompt,
            strength=0.68,
        )
        candidate = generate_region_take(
            source,
            request,
            work_dir / track_id,
            1,
        )
        candidate = _safe_project_path(project, candidate)
        if not candidate.is_file():
            raise RuntimeError(f"Section renderer did not produce a candidate for track {track_id!r}")
        batch.tracks.append(
            SectionTrackCandidate(
                layer_id=layer.id,
                track_id=track_id,
                base_clip_id=base.id,
                candidate_path=str(candidate),
                section_start_seconds=overlap_start,
                section_end_seconds=overlap_end,
                source_relative_start=relative_start,
                source_relative_end=relative_end,
            )
        )

    if not batch.tracks:
        raise RuntimeError("No editable DAW-linked audio layers overlap the selected section")
    return batch


def stage_section_candidate_batch(
    session: StudioSession,
    batch: SectionCandidateBatch,
    *,
    project: Path,
) -> StudioSession:
    """Return an all-or-nothing staged session containing the candidate take set.

    Every source identity and candidate file is validated before the deep-copied session is
    mutated. The caller must still render, master and pass the existing release-quality gate
    before saving this staged session over the authoritative DAW session.
    """
    if not batch.tracks:
        raise RuntimeError("Section candidate batch is empty")

    validated: list[tuple[SectionTrackCandidate, Clip, Path]] = []
    seen_tracks: set[str] = set()
    for item in batch.tracks:
        if item.track_id in seen_tracks:
            raise RuntimeError(f"Section candidate batch contains duplicate DAW track {item.track_id!r}")
        seen_tracks.add(item.track_id)
        base = _active_clip(session, item.track_id)
        if base.id != item.base_clip_id:
            raise RuntimeError(f"DAW track {item.track_id!r} changed after candidate generation; regenerate before commit")
        candidate = _safe_project_path(project, item.candidate_path)
        if not candidate.is_file():
            raise RuntimeError(f"Section candidate is missing for track {item.track_id!r}")
        validated.append((item, base, candidate))

    staged = session.model_copy(deep=True)
    committed_clip_ids: list[str] = []
    for item, base, candidate in validated:
        track = staged.find_track(item.track_id)
        for clip in track.clips:
            clip.metadata["committed"] = False
        take = Clip(
            name=f"Aura Section Regeneration — {batch.section_id}",
            kind="audio",
            source=str(candidate),
            start=base.start,
            duration=base.duration,
            source_offset=base.source_offset,
            gain_db=base.gain_db,
            fade_in=base.fade_in,
            fade_out=base.fade_out,
            take_lane=max((clip.take_lane for clip in track.clips), default=-1) + 1,
            metadata={
                "real_audio": True,
                "committed": True,
                "song_dna_directive": batch.directive_id,
                "section_id": batch.section_id,
                "section_regeneration_batch": True,
            },
        )
        track.clips.append(take)
        committed_clip_ids.append(take.id)

    staged.generation_history.append(
        {
            "action": "song_dna_section_regeneration_stage",
            "directive_id": batch.directive_id,
            "section_id": batch.section_id,
            "track_ids": [item.track_id for item in batch.tracks],
            "clip_ids": committed_clip_ids,
            "candidate_count": len(batch.tracks),
            "preserve_instrument_identity": batch.preserve_instrument_identity,
            "persisted": False,
        }
    )
    staged.touch()
    return staged


__all__ = [
    "SectionCandidateBatch",
    "SectionTrackCandidate",
    "generate_section_candidate_batch",
    "stage_section_candidate_batch",
]

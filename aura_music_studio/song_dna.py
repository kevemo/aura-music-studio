from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .content_safety import enforce_creation_policy


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str, fallback: str = "section") -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return value or fallback


class SongSection(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(default_factory=lambda: f"sec_{uuid4().hex}")
    name: str
    kind: str = "section"
    order: int = 0
    start_seconds: float | None = None
    end_seconds: float | None = None
    prompt: str = ""
    locked: bool = False
    metadata: dict = Field(default_factory=dict)


class LyricLine(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(default_factory=lambda: f"lyr_{uuid4().hex}")
    section_id: str | None = None
    order: int = 0
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    locked: bool = False
    revision: int = 1
    metadata: dict = Field(default_factory=dict)


class InstrumentLayer(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(default_factory=lambda: f"inst_{uuid4().hex}")
    role: str
    label: str
    enabled: bool = True
    track_id: str | None = None
    source_ref: str | None = None
    stem_ref: str | None = None
    midi_ref: str | None = None
    sound_identity: str = ""
    prompt: str = ""
    locked: bool = False
    metadata: dict = Field(default_factory=dict)


EditAction = Literal[
    "replace_lyric_line",
    "replace_instrument",
    "add_instrument",
    "remove_instrument",
    "regenerate_section",
    "extend_section",
    "shorten_section",
    "change_key",
    "change_tempo",
    "change_voice",
    "remix",
    "remaster",
]


class SongEditDirective(BaseModel):
    id: str = Field(default_factory=lambda: f"edit_{uuid4().hex}")
    action: EditAction
    instruction: str
    target_ids: list[str] = Field(default_factory=list)
    preserve_ids: list[str] = Field(default_factory=list)
    status: Literal["planned", "ready", "queued", "rendering", "complete", "failed", "cancelled"] = "planned"
    renderer_route: str = "music_audio_stack"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    metadata: dict = Field(default_factory=dict)


class SongDNA(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    schema_version: int = 1
    project_name: str
    title: str
    version: int = 1
    genre: str = ""
    mood: str = ""
    language: str = "English"
    bpm: float | None = None
    key: str | None = None
    meter: str = "4/4"
    target_duration_seconds: float | None = None
    structure_text: str = ""
    lyrics_raw: str = ""
    sections: list[SongSection] = Field(default_factory=list)
    lyric_lines: list[LyricLine] = Field(default_factory=list)
    instruments: list[InstrumentLayer] = Field(default_factory=list)
    vocal_mode: str = "ai_vocal"
    voice_profile_id: str | None = None
    master_profile: dict = Field(default_factory=dict)
    quality_contract: dict = Field(default_factory=dict)
    directives: list[SongEditDirective] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


_HEADER = re.compile(r"^\s*\[?\s*(verse(?:\s+\d+)?|pre[- ]?chorus|chorus|bridge|intro|outro|breakdown|solo|hook|refrain|interlude|final chorus)\s*\]?\s*:?[ \t]*$", re.I)


def _structure_sections(structure: str) -> list[SongSection]:
    names = [x.strip() for x in (structure or "").split(",") if x.strip()]
    if not names:
        names = ["Song"]
    out: list[SongSection] = []
    counts: dict[str, int] = {}
    for index, name in enumerate(names):
        base = _slug(name)
        counts[base] = counts.get(base, 0) + 1
        suffix = f"-{counts[base]}" if counts[base] > 1 else ""
        out.append(SongSection(id=f"sec_{base}{suffix}", name=name, kind=base, order=index))
    return out


def _parse_lyrics(lyrics: str, sections: list[SongSection]) -> list[LyricLine]:
    raw = (lyrics or "").strip()
    if not raw:
        return []
    section_by_name = {_slug(section.name): section for section in sections}
    blocks = [block.strip() for block in re.split(r"\n\s*\n", raw) if block.strip()]
    lines: list[LyricLine] = []
    order = 0
    current: SongSection | None = None
    block_index = 0
    for block in blocks:
        block_lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not block_lines:
            continue
        first_match = _HEADER.match(block_lines[0])
        if first_match:
            key = _slug(first_match.group(1))
            current = section_by_name.get(key)
            if current is None:
                current = SongSection(id=f"sec_{key}-{len(sections)+1}", name=first_match.group(1).title(), kind=key, order=len(sections))
                sections.append(current)
                section_by_name[key] = current
            block_lines = block_lines[1:]
        elif current is None:
            current = sections[min(block_index, len(sections)-1)] if sections else None
        for text in block_lines:
            lines.append(LyricLine(section_id=current.id if current else None, order=order, text=text))
            order += 1
        block_index += 1
    return lines


def _default_quality_contract() -> dict:
    return {
        "standard": "release_grade_editable_master",
        "require_real_audio": True,
        "symbolic_guide_allowed_as_final": False,
        "preferred_sample_rate": 48000,
        "preferred_bit_depth": 24,
        "perceptual_review_required": True,
        "human_performance_targets": [
            "natural expressive vocals",
            "believable dynamics and timing",
            "realistic articulation and transients",
            "stable instrument identity",
            "clean arrangement and mix separation",
        ],
        "editable_deliverables": [
            "lyrics",
            "sections",
            "instrument roster",
            "stems when available",
            "MIDI/transcription when available",
            "effects/routing/master settings",
            "revision history",
        ],
    }


def create_song_dna(
    project: Path,
    *,
    project_name: str,
    title: str,
    genre: str = "",
    mood: str = "",
    language: str = "English",
    bpm: float | None = None,
    key: str | None = None,
    meter: str = "4/4",
    target_duration_seconds: float | None = None,
    structure: str = "",
    lyrics: str = "",
    instruments: list[str] | None = None,
    vocal_mode: str = "ai_vocal",
    voice_profile_id: str | None = None,
    master_profile: dict | None = None,
    metadata: dict | None = None,
    overwrite: bool = False,
) -> SongDNA:
    store = SongDNAStore(project)
    if store.path.is_file() and not overwrite:
        return store.load()
    sections = _structure_sections(structure)
    lyric_lines = _parse_lyrics(lyrics, sections)
    layers = [
        InstrumentLayer(role=_slug(name, "instrument"), label=name, sound_identity=name, prompt=f"Real performed-sounding {name}")
        for name in (instruments or [])
    ]
    dna = SongDNA(
        project_name=project_name,
        title=title,
        genre=genre,
        mood=mood,
        language=language,
        bpm=bpm,
        key=key,
        meter=meter,
        target_duration_seconds=target_duration_seconds,
        structure_text=structure,
        lyrics_raw=(lyrics or "").strip(),
        sections=sections,
        lyric_lines=lyric_lines,
        instruments=layers,
        vocal_mode=vocal_mode,
        voice_profile_id=voice_profile_id,
        master_profile=master_profile or {},
        quality_contract=_default_quality_contract(),
        metadata=metadata or {},
    )
    store.save(dna)
    return dna


def ensure_song_dna_from_manifest(project: Path, manifest: dict) -> SongDNA:
    store = SongDNAStore(project)
    if store.path.is_file():
        return store.load()
    project_dna = dict(manifest.get("project_dna") or {})
    lyrics = ""
    lyrics_ref = manifest.get("lyrics_file")
    if lyrics_ref:
        lyrics_path = (project / str(lyrics_ref)).resolve()
        try:
            if project.resolve() in lyrics_path.parents and lyrics_path.is_file():
                lyrics = lyrics_path.read_text(encoding="utf-8")
        except Exception:
            lyrics = ""
    genre_preset = project_dna.get("genre_preset") or {}
    return create_song_dna(
        project,
        project_name=str(manifest.get("project_name") or project.name),
        title=str(manifest.get("title") or project.name),
        genre=str(genre_preset.get("id") or genre_preset.get("name") or ""),
        mood=str(project_dna.get("mood") or ""),
        language=str(project_dna.get("language") or "English"),
        bpm=manifest.get("tempo_bpm"),
        key=manifest.get("key"),
        meter=str(manifest.get("meter") or "4/4"),
        target_duration_seconds=manifest.get("target_duration_seconds"),
        structure=str(project_dna.get("structure") or ""),
        lyrics=lyrics,
        instruments=list(project_dna.get("requested_instruments") or []),
        vocal_mode=str(project_dna.get("vocal_mode") or "ai_vocal"),
        voice_profile_id=project_dna.get("voice_profile_id"),
        master_profile=dict(manifest.get("mix") or {}),
        metadata={"initialized_from": "project_manifest"},
    )


class SongDNAStore:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.path = self.project / "song_dna.json"

    def load(self) -> SongDNA:
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        return SongDNA.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, dna: SongDNA) -> SongDNA:
        dna.updated_at = _now()
        tmp = self.path.with_suffix(".json.tmp")
        self.project.mkdir(parents=True, exist_ok=True)
        tmp.write_text(dna.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.path)
        return dna

    def render_lyrics(self, dna: SongDNA) -> str:
        section_map = {section.id: section for section in dna.sections}
        grouped: dict[str | None, list[LyricLine]] = {}
        for line in sorted(dna.lyric_lines, key=lambda item: item.order):
            grouped.setdefault(line.section_id, []).append(line)
        blocks: list[str] = []
        ordered_ids = [section.id for section in sorted(dna.sections, key=lambda item: item.order)]
        for section_id in ordered_ids + ([None] if None in grouped else []):
            lines = grouped.get(section_id) or []
            if not lines:
                continue
            header = section_map.get(section_id).name if section_id and section_id in section_map else "Lyrics"
            blocks.append(f"[{header}]\n" + "\n".join(line.text for line in lines))
        return "\n\n".join(blocks).strip()

    def update_lyric_line(self, line_id: str, text: str) -> SongDNA:
        clean = (text or "").strip()
        if not clean:
            raise ValueError("Lyric line cannot be empty; use a section/edit operation to remove a line")
        enforce_creation_policy(clean, context="Song lyric edit")
        dna = self.load()
        line = next((item for item in dna.lyric_lines if item.id == line_id), None)
        if line is None:
            raise KeyError(line_id)
        if line.locked:
            raise ValueError("This lyric line is locked")
        before = line.text
        line.text = clean
        line.revision += 1
        dna.version += 1
        dna.lyrics_raw = self.render_lyrics(dna)
        dna.directives.append(SongEditDirective(
            action="replace_lyric_line",
            instruction=f"Replace only lyric line {line_id}; preserve the rest of the song performance and arrangement unless timing requires a local phrase repair.",
            target_ids=[line_id],
            preserve_ids=[item.id for item in dna.lyric_lines if item.id != line_id],
            status="planned",
            metadata={"before": before, "after": clean, "local_regeneration_preferred": True},
        ))
        lyrics_path = self.project / "input" / "lyrics.txt"
        lyrics_path.parent.mkdir(parents=True, exist_ok=True)
        lyrics_path.write_text(dna.lyrics_raw + "\n", encoding="utf-8")
        return self.save(dna)

    def plan_instrument_replacement(self, layer_id: str, replacement: str, instruction: str = "") -> SongDNA:
        clean = (replacement or "").strip()
        if not clean:
            raise ValueError("Replacement instrument is required")
        enforce_creation_policy(clean, instruction, context="Instrument replacement")
        dna = self.load()
        layer = next((item for item in dna.instruments if item.id == layer_id), None)
        if layer is None:
            raise KeyError(layer_id)
        if layer.locked:
            raise ValueError("This instrument layer is locked")
        preserve = [item.id for item in dna.instruments if item.id != layer_id and item.locked]
        dna.directives.append(SongEditDirective(
            action="replace_instrument",
            instruction=instruction.strip() or f"Replace {layer.label} with {clean}. Preserve song structure, lyrics, vocal identity and every locked layer.",
            target_ids=[layer_id],
            preserve_ids=preserve,
            status="planned",
            metadata={
                "current_role": layer.role,
                "current_label": layer.label,
                "replacement": clean,
                "track_id": layer.track_id,
                "source_ref": layer.source_ref,
                "stem_ref": layer.stem_ref,
            },
        ))
        dna.version += 1
        return self.save(dna)

    def plan_section_regeneration(self, section_id: str, instruction: str, *, preserve_instruments: bool = True) -> SongDNA:
        clean = (instruction or "").strip()
        if not clean:
            raise ValueError("Section regeneration instruction is required")
        enforce_creation_policy(clean, context="Song section regeneration")
        dna = self.load()
        section = next((item for item in dna.sections if item.id == section_id), None)
        if section is None:
            raise KeyError(section_id)
        if section.locked:
            raise ValueError("This section is locked")
        preserve = [item.id for item in dna.instruments if item.locked or preserve_instruments]
        preserve += [item.id for item in dna.sections if item.id != section_id and item.locked]
        dna.directives.append(SongEditDirective(
            action="regenerate_section",
            instruction=clean,
            target_ids=[section_id],
            preserve_ids=preserve,
            status="planned",
            metadata={"section_name": section.name, "local_regeneration_required": True},
        ))
        dna.version += 1
        return self.save(dna)

    def sync_session(self, session_path: Path) -> SongDNA:
        from .session import StudioSession

        dna = self.load()
        if not session_path.is_file():
            return dna
        session = StudioSession.load(session_path)
        existing_by_track = {item.track_id: item for item in dna.instruments if item.track_id}
        existing_by_role = {item.role: item for item in dna.instruments}
        for track in session.tracks:
            if track.role in {"master", "bus"}:
                continue
            layer = existing_by_track.get(track.id) or existing_by_role.get(track.role)
            source_ref = next((clip.source for clip in track.clips if clip.kind == "audio" and clip.source), None)
            midi_ref = next((clip.source for clip in track.clips if clip.kind == "midi" and clip.source), None)
            if layer is None:
                layer = InstrumentLayer(role=track.role, label=track.name, track_id=track.id, source_ref=source_ref, midi_ref=midi_ref)
                dna.instruments.append(layer)
            else:
                layer.track_id = track.id
                layer.label = track.name
                if source_ref:
                    layer.source_ref = source_ref
                if midi_ref:
                    layer.midi_ref = midi_ref
                layer.metadata["effects"] = [effect.model_dump(mode="json") for effect in track.effects]
                layer.metadata["automation"] = [lane.model_dump(mode="json") for lane in track.automation]
        dna.bpm = session.bpm
        dna.key = session.key
        dna.meter = session.meter
        dna.metadata["session_id"] = session.id
        dna.metadata["session_modified_at"] = session.modified_at
        return self.save(dna)

    def sync_exports(self, master_wav: str | None, stems: list[str] | None) -> SongDNA:
        dna = self.load()
        if master_wav:
            dna.metadata["latest_master_wav"] = master_wav
        stem_paths = list(stems or [])
        for path in stem_paths:
            name = Path(path).stem.lower()
            for layer in dna.instruments:
                role = layer.role.lower()
                label = layer.label.lower()
                if role in name or any(token in name for token in label.split() if len(token) > 3):
                    layer.stem_ref = path
        dna.metadata["latest_stems"] = stem_paths
        return self.save(dna)

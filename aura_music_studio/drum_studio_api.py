from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from .daw import save_session
from .daw_midi import (
    _create_clip,
    _member,
    _project,
    _public_clip,
    _save_document,
    _session,
    _snapshot,
)
from .drum_studio import (
    DRUM_STUDIO_ENGINE_VERSION,
    GM_DRUM_CHANNEL,
    GM_DRUM_NOTES,
    DrumPattern,
    four_on_the_floor,
    pattern_to_midi_document,
)

router = APIRouter(tags=["Music Drum Studio"])


def _pattern_manifest_path(project: Path, pattern_id: str) -> Path:
    root = (project / "work" / "drum_patterns").resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / f"{pattern_id}.json").resolve()
    if root not in target.parents:
        raise ValueError("Drum pattern escaped the project boundary")
    return target


def _persist_pattern(project: Path, pattern_id: str, payload: dict) -> str:
    target = _pattern_manifest_path(project, pattern_id)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target.resolve().relative_to(project.resolve()).as_posix()


@router.get("/projects/{project_name}/daw/drums")
def drum_studio_capabilities(project_name: str, request: Request):
    _member(request)
    project = _project(project_name)
    session = _session(project)
    return {
        "engine_version": DRUM_STUDIO_ENGINE_VERSION,
        "bpm": session.bpm,
        "midi_channel": GM_DRUM_CHANNEL,
        "lanes": [{"id": name, "midi_note": note} for name, note in GM_DRUM_NOTES.items()],
        "grid_steps_per_bar": [8, 16, 32],
        "features": {
            "step_sequencer": True,
            "velocity": True,
            "probability": True,
            "micro_timing": True,
            "swing": True,
            "seeded_humanisation": True,
            "custom_percussion_notes": True,
            "piano_roll_editable": True,
        },
        "symbolic_guide_only": True,
        "audio_rendered": False,
        "final_audio": False,
    }


@router.post("/projects/{project_name}/daw/drums/patterns")
def create_drum_pattern(project_name: str, body: DrumPattern, request: Request):
    member = _member(request)
    project = _project(project_name)
    session = _session(project)
    try:
        document, report = pattern_to_midi_document(body, bpm=float(session.bpm or 120.0))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    pattern_id = f"drums_{uuid4().hex}"
    source = project / "input" / "midi" / "drums" / f"{pattern_id}.mid"
    _snapshot(project, member, f"Before creating drum pattern · {body.name}")
    track, clip = _create_clip(project, session, name=body.name, track_id=None, source=source)
    _save_document(project, session, clip, document)
    clip.metadata.update(
        {
            "drum_studio": True,
            "drum_pattern_id": pattern_id,
            "drum_engine_version": DRUM_STUDIO_ENGINE_VERSION,
            "bars": body.bars,
            "steps_per_bar": body.steps_per_bar,
            "swing": body.swing,
            "seed": body.seed,
            "symbolic_guide_only": True,
            "audio_rendered": False,
            "final_audio": False,
        }
    )
    session.generation_history.append(
        {
            "action": "drum_pattern_create",
            "clip_id": clip.id,
            "track_id": track.id,
            "drum_pattern_id": pattern_id,
            **report.as_dict(),
        }
    )
    save_session(project, session)

    manifest = {
        "pattern_id": pattern_id,
        "engine_version": DRUM_STUDIO_ENGINE_VERSION,
        "pattern": body.model_dump(mode="json"),
        "report": report.as_dict(),
        "midi_ref": source.resolve().relative_to(project.resolve()).as_posix(),
        "clip_id": clip.id,
        "track_id": track.id,
        "symbolic_guide_only": True,
        "audio_rendered": False,
        "final_audio": False,
    }
    manifest_ref = _persist_pattern(project, pattern_id, manifest)
    return {
        "pattern_id": pattern_id,
        "manifest_ref": manifest_ref,
        "clip": _public_clip(project_name, track, clip, document),
        "document": document.model_dump(mode="json"),
        "report": report.as_dict(),
        "symbolic_guide_only": True,
        "audio_rendered": False,
        "final_audio": False,
    }


@router.post("/projects/{project_name}/daw/drums/starter/four-on-the-floor")
def create_four_on_floor(project_name: str, request: Request, bars: int = 1, steps_per_bar: int = 16, seed: int = 0):
    try:
        pattern = four_on_the_floor(bars=bars, steps_per_bar=steps_per_bar, seed=seed)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return create_drum_pattern(project_name, pattern, request)


__all__ = ["router"]

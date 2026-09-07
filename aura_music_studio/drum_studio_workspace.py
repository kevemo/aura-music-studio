from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from .daw import load_session, save_session
from .daw_midi import _create_clip, _public_clip, _save_document
from .drum_studio import DrumPattern, four_on_the_floor, pattern_to_midi_document

_PATTERN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _root(project: Path) -> Path:
    root = project.resolve() / "work" / "drum_studio" / "patterns"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _pattern_path(project: Path, pattern_id: str) -> Path:
    clean = str(pattern_id or "").strip()
    if not _PATTERN_ID.fullmatch(clean):
        raise ValueError("Invalid Drum Studio pattern id")
    root = _root(project)
    target = (root / f"{clean}.json").resolve()
    if root.resolve() not in target.parents:
        raise ValueError("Drum Studio pattern path escapes the project")
    return target


def save_pattern(project: Path, pattern: DrumPattern, *, pattern_id: str | None = None) -> dict:
    """Persist one validated Drum Studio pattern inside the member project."""
    pattern_id = pattern_id or uuid4().hex
    path = _pattern_path(project, pattern_id)
    payload = {
        "schema_version": 1,
        "pattern_id": pattern_id,
        "pattern": pattern.model_dump(mode="json"),
        "symbolic_guide_only": True,
        "final_audio": False,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return public_pattern(payload)


def load_pattern(project: Path, pattern_id: str) -> tuple[DrumPattern, dict]:
    path = _pattern_path(project, pattern_id)
    if not path.is_file():
        raise KeyError(pattern_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported Drum Studio pattern schema")
    pattern = DrumPattern.model_validate(payload.get("pattern") or {})
    return pattern, payload


def list_patterns(project: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(_root(project).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pattern = DrumPattern.model_validate(payload.get("pattern") or {})
        except Exception:
            continue
        rows.append(
            {
                "pattern_id": str(payload.get("pattern_id") or path.stem),
                "name": pattern.name,
                "bars": pattern.bars,
                "steps_per_bar": pattern.steps_per_bar,
                "swing": pattern.swing,
                "lane_count": len(pattern.lanes),
                "hit_count": sum(len(lane.hits) for lane in pattern.lanes),
                "symbolic_guide_only": True,
                "final_audio": False,
            }
        )
    return rows


def public_pattern(payload: dict) -> dict:
    pattern = DrumPattern.model_validate(payload.get("pattern") or {})
    return {
        "pattern_id": str(payload.get("pattern_id") or ""),
        "pattern": pattern.model_dump(mode="json"),
        "symbolic_guide_only": True,
        "final_audio": False,
        "storage_path_exposed": False,
    }


def starter_pattern(project: Path, *, bars: int = 1, steps_per_bar: int = 16, seed: int = 0) -> dict:
    return save_pattern(project, four_on_the_floor(bars=bars, steps_per_bar=steps_per_bar, seed=seed))


def insert_pattern_into_daw(
    project: Path,
    pattern_id: str,
    *,
    track_id: str | None = None,
) -> dict:
    """Render stored drum control data to MIDI and insert it into the existing DAW session."""
    pattern, _payload = load_pattern(project, pattern_id)
    session = load_session(project)
    document, report = pattern_to_midi_document(pattern, bpm=session.bpm)
    if not document.notes:
        raise ValueError("Drum pattern did not render any MIDI hits")

    output = project.resolve() / "input" / "midi" / f"drum-{pattern_id}-{uuid4().hex[:10]}.mid"
    try:
        track, clip = _create_clip(
            project.resolve(),
            session,
            name=pattern.name,
            track_id=track_id,
            source=output,
        )
        _save_document(project.resolve(), session, clip, document)
        clip.metadata.update(
            {
                "drum_studio": True,
                "drum_pattern_id": pattern_id,
                "drum_engine_version": report.engine_version,
                "swing": report.swing,
                "seed": report.seed,
                "rendered_hits": report.rendered_hits,
                "symbolic_guide_only": True,
                "final_audio": False,
            }
        )
        session.generation_history.append(
            {
                "action": "drum_studio_insert",
                "clip_id": clip.id,
                "track_id": track.id,
                "pattern_id": pattern_id,
                "engine_version": report.engine_version,
                "rendered_hits": report.rendered_hits,
                "symbolic_guide_only": True,
            }
        )
        save_session(project.resolve(), session)
    except Exception:
        output.unlink(missing_ok=True)
        raise

    return {
        "pattern_id": pattern_id,
        "clip": _public_clip(project.name, track, clip, document),
        "report": report.as_dict(),
        "symbolic_guide_only": True,
        "final_audio": False,
        "storage_path_exposed": False,
    }


__all__ = [
    "insert_pattern_into_daw",
    "list_patterns",
    "load_pattern",
    "public_pattern",
    "save_pattern",
    "starter_pattern",
]

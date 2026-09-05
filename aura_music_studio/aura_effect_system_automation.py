from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable

from .daw import load_session, save_session
from .revisions import create_revision, restore_revision
from .session import AutomationLane, AutomationPoint

_MAX_AUTOMATION_POINTS = 2000
_ALLOWED_INTERPOLATION = {"hold", "linear", "smooth"}
_ALLOWED_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _bounded_id(value: str, *, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label} is required")
    if len(clean) > 120:
        raise ValueError(f"{label} is too long")
    if any(ch not in _ALLOWED_ID_CHARS for ch in clean):
        raise ValueError(f"{label} contains unsupported characters")
    return clean


def _normalized_points(points: Iterable[dict[str, Any] | AutomationPoint]) -> list[AutomationPoint]:
    raw = list(points)
    if len(raw) > _MAX_AUTOMATION_POINTS:
        raise ValueError(f"Effect automation exceeds {_MAX_AUTOMATION_POINTS} points")
    parsed: list[AutomationPoint] = []
    for value in raw:
        if isinstance(value, AutomationPoint):
            point = value
        elif isinstance(value, dict):
            try:
                point = AutomationPoint(time=float(value.get("time")), value=float(value.get("value")))
            except (TypeError, ValueError) as exc:
                raise ValueError("Automation points require finite numeric time and value") from exc
        else:
            raise ValueError("Each automation point must be an object")
        if not math.isfinite(float(point.time)) or not math.isfinite(float(point.value)):
            raise ValueError("Automation points require finite numeric time and value")
        parsed.append(point)
    return parsed


def _history_rows(track) -> list[dict[str, Any]]:
    rows = track.metadata.get("effect_systems")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _resolve_applied_effect(track, system_id: str, node_id: str) -> tuple[str, dict[str, Any]]:
    system_id = _bounded_id(system_id, label="Effect system id")
    node_id = _bounded_id(node_id, label="Effect node id")
    available_effect_ids = {effect.id for effect in track.effects}

    for row in reversed(_history_rows(track)):
        if str(row.get("system_id") or "") != system_id:
            continue
        fingerprint = str(row.get("fingerprint") or "").strip().casefold()
        if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
            continue
        expected = f"system.{fingerprint[:12]}.{node_id}"
        effect_ids = row.get("effect_ids")
        if not isinstance(effect_ids, list) or expected not in effect_ids:
            continue
        if expected not in available_effect_ids:
            raise ValueError("Applied effect-system node is no longer present on the track")
        return expected, row
    raise ValueError("Applied effect-system node was not found on this track")


def effect_system_mix_automation(
    project: Path,
    track_id: str,
    system_id: str,
    node_id: str,
) -> dict[str, Any]:
    session = load_session(project)
    try:
        track = session.find_track(_bounded_id(track_id, label="Track id"))
    except KeyError as exc:
        raise ValueError("Track was not found") from exc
    effect_id, history = _resolve_applied_effect(track, system_id, node_id)
    parameter = f"fx:{effect_id}:mix"
    lane = next((row for row in track.automation if row.parameter == parameter), None)
    return {
        "project": project.name,
        "track_id": track.id,
        "system_id": str(history.get("system_id") or system_id),
        "system_version": int(history.get("version") or 1),
        "system_fingerprint": str(history.get("fingerprint") or ""),
        "node_id": node_id,
        "effect_id": effect_id,
        "parameter": parameter,
        "exists": lane is not None,
        "interpolation": lane.interpolation if lane else "linear",
        "points": [point.model_dump(mode="json") for point in lane.points] if lane else [],
        "source_media_mutated": False,
    }


def set_effect_system_mix_automation(
    project: Path,
    track_id: str,
    system_id: str,
    node_id: str,
    points: Iterable[dict[str, Any] | AutomationPoint],
    *,
    interpolation: str = "linear",
    actor: str = "Studio member",
    keep_revisions: int = 40,
) -> dict[str, Any]:
    """Create or replace one applied system node's wet/dry automation lane.

    The DAW session already defines ``fx:<effect-id>:mix`` as a renderer-supported 0..1
    automation path. This workflow binds an Aura Effect/System Creator node to that existing
    primitive, snapshots the project before mutation and persists only project metadata.
    """
    interpolation = str(interpolation or "linear").strip().lower()
    if interpolation not in _ALLOWED_INTERPOLATION:
        raise ValueError("Interpolation must be hold, linear or smooth")
    parsed_points = _normalized_points(points)

    session = load_session(project)
    try:
        track = session.find_track(_bounded_id(track_id, label="Track id"))
    except KeyError as exc:
        raise ValueError("Track was not found") from exc
    effect_id, history = _resolve_applied_effect(track, system_id, node_id)
    parameter = f"fx:{effect_id}:mix"

    # Construct first so normalization/clamping errors occur before revision creation.
    lane = AutomationLane(parameter=parameter, points=parsed_points, interpolation=interpolation)
    revision = create_revision(
        project,
        label=f"Before effect automation {system_id}:{node_id}"[:160],
        reason="effect_system_automation",
        actor=(str(actor or "Studio member").strip() or "Studio member")[:120],
        keep=max(1, int(keep_revisions)),
    )

    track.automation = [row for row in track.automation if row.parameter != parameter]
    track.automation.append(lane)
    save_session(project, session)
    return {
        "updated": True,
        "project": project.name,
        "track_id": track.id,
        "system_id": str(history.get("system_id") or system_id),
        "system_version": int(history.get("version") or 1),
        "system_fingerprint": str(history.get("fingerprint") or ""),
        "node_id": node_id,
        "effect_id": effect_id,
        "parameter": lane.parameter,
        "interpolation": lane.interpolation,
        "points": [point.model_dump(mode="json") for point in lane.points],
        "revision_id": revision["id"],
        "project_metadata_mutated": True,
        "source_media_mutated": False,
        "undo_available": True,
    }


def clear_effect_system_mix_automation(
    project: Path,
    track_id: str,
    system_id: str,
    node_id: str,
    *,
    actor: str = "Studio member",
    keep_revisions: int = 40,
) -> dict[str, Any]:
    session = load_session(project)
    try:
        track = session.find_track(_bounded_id(track_id, label="Track id"))
    except KeyError as exc:
        raise ValueError("Track was not found") from exc
    effect_id, history = _resolve_applied_effect(track, system_id, node_id)
    parameter = f"fx:{effect_id}:mix"
    if not any(row.parameter == parameter for row in track.automation):
        return {
            "cleared": False,
            "already_clear": True,
            "project": project.name,
            "track_id": track.id,
            "system_id": str(history.get("system_id") or system_id),
            "node_id": node_id,
            "effect_id": effect_id,
            "parameter": parameter,
            "revision_id": None,
            "source_media_mutated": False,
        }

    revision = create_revision(
        project,
        label=f"Before clearing effect automation {system_id}:{node_id}"[:160],
        reason="effect_system_automation_clear",
        actor=(str(actor or "Studio member").strip() or "Studio member")[:120],
        keep=max(1, int(keep_revisions)),
    )
    track.automation = [row for row in track.automation if row.parameter != parameter]
    save_session(project, session)
    return {
        "cleared": True,
        "already_clear": False,
        "project": project.name,
        "track_id": track.id,
        "system_id": str(history.get("system_id") or system_id),
        "node_id": node_id,
        "effect_id": effect_id,
        "parameter": parameter,
        "revision_id": revision["id"],
        "project_metadata_mutated": True,
        "source_media_mutated": False,
        "undo_available": True,
    }


def restore_effect_system_automation_revision(
    project: Path,
    revision_id: str,
    *,
    keep_revisions: int = 40,
) -> dict[str, Any]:
    result = restore_revision(
        project,
        _bounded_id(revision_id, label="Revision id"),
        create_backup=True,
        keep=max(1, int(keep_revisions)),
    )
    return {
        **result,
        "effect_system_automation_undo": True,
        "source_media_mutated": False,
    }


__all__ = [
    "clear_effect_system_mix_automation",
    "effect_system_mix_automation",
    "restore_effect_system_automation_revision",
    "set_effect_system_mix_automation",
]

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import professional_captions as base
from .professional_editor_api import _actor, _member, _state, _store

router = APIRouter(prefix="/creative", tags=["Professional Captions and Subtitles"])
_INSTALLED = False


def _caption_context(store, item_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = store.public_state()["branch"]
    item = next((row for row in state.get("items", []) if row.get("id") == item_id), None)
    if item is None or not (item.get("metadata") or {}).get("caption_cue"):
        raise KeyError(item_id)
    track = next((row for row in state.get("tracks", []) if item_id in row.get("item_ids", [])), None)
    if track is None or not (track.get("metadata") or {}).get("caption_track"):
        raise KeyError(item_id)
    sequence = next((row for row in state.get("sequences", []) if track["id"] in row.get("track_ids", [])), None)
    if sequence is None or sequence.get("kind") != "video":
        raise KeyError(item_id)
    return item, track, sequence


@router.patch("/projects/{project_name}/editor/captions/{item_id}")
def patch_caption_hardened(
    project_name: str,
    item_id: str,
    body: base.CaptionPatchRequest,
    request: Request,
):
    member = _member(request)
    store = _store(project_name)
    try:
        current, _track, sequence = _caption_context(store, item_id)
        start = float(body.start if body.start is not None else current["start"])
        current_end = float(current["start"]) + float(current["duration"])
        end = float(body.end if body.end is not None else current_end)
        if end <= start:
            raise ValueError("Caption cue end must be after start")
        if end > float(sequence["duration"]) + 1e-8:
            raise ValueError("Caption cue extends beyond the video sequence duration")

        changes: dict[str, Any] = {"start": start, "duration": end - start}
        content = str((current.get("text") or {}).get("content") or current.get("name") or "Caption")
        if body.text is not None:
            content = base._clean_text(body.text)
            changes["text"] = {"content": content}
        if body.style is not None:
            changes["text"] = base._text_payload(content, body.style)
            changes["transform"] = {
                "x": 0.0,
                "y": base._position_y(int(sequence["height"]), body.style.position),
            }
            changes["metadata"] = {"caption_position": body.style.position}

        updated = store.patch_item(item_id, changes, actor=_actor(member))
    except KeyError as exc:
        raise HTTPException(404, "Caption cue not found") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"cue": updated.model_dump(mode="json"), "editor": _state(store)}


def install_professional_captions_hardening() -> None:
    """Install the bounded caption edit route ahead of the initial route definition."""
    global _INSTALLED
    if _INSTALLED:
        return
    hardened = list(router.routes)
    existing = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set())), getattr(route, "endpoint", None))
        for route in base.router.routes
    }
    for route in reversed(hardened):
        signature = (
            getattr(route, "path", None),
            frozenset(getattr(route, "methods", set())),
            getattr(route, "endpoint", None),
        )
        if signature not in existing:
            base.router.routes.insert(0, route)
    _INSTALLED = True


__all__ = ["install_professional_captions_hardening", "patch_caption_hardened", "router"]

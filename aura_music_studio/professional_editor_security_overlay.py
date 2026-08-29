from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from .professional_editor_api import (
    PatchRequest,
    _actor,
    _execute,
    _member,
    _state,
    _store,
    router as professional_editor_router,
)
from .professional_editor_source_security import normalize_project_source_ref


router = APIRouter(prefix="/creative", tags=["Professional Creative Editor Security"])


def normalize_item_patch_sources(project_dir: Path, changes: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of item patch changes with source_ref confined to the project.

    The base professional-editor route historically accepted a generic patch dictionary.
    Source creation/import is already confined, but a later patch must pass through the same
    project-source boundary before any state mutation can occur.
    """

    normalized = deepcopy(changes)
    if "source_ref" in normalized:
        normalized["source_ref"] = normalize_project_source_ref(
            Path(project_dir), normalized.get("source_ref")
        )
    return normalized


@router.patch("/projects/{project_name}/editor/items/{item_id}")
def patch_item_source_guard(
    project_name: str,
    item_id: str,
    body: PatchRequest,
    request: Request,
):
    member = _member(request)
    store = _store(project_name)
    changes = _execute(
        lambda: normalize_item_patch_sources(store.project_dir, body.changes)
    )
    item = _execute(
        lambda: store.patch_item(item_id, changes, actor=_actor(member))
    )
    return {"item": item.model_dump(mode="json"), "editor": _state(store)}


def install_professional_editor_patch_guard() -> None:
    """Prepend the guarded PATCH route so FastAPI cannot reach the legacy generic route first."""

    guarded_routes = list(router.routes)
    for guarded in reversed(guarded_routes):
        signature = (getattr(guarded, "path", None), frozenset(getattr(guarded, "methods", set())))
        already_installed = any(
            (getattr(existing, "path", None), frozenset(getattr(existing, "methods", set()))) == signature
            and getattr(existing, "endpoint", None) is getattr(guarded, "endpoint", None)
            for existing in professional_editor_router.routes
        )
        if not already_installed:
            professional_editor_router.routes.insert(0, guarded)


__all__ = [
    "router",
    "normalize_item_patch_sources",
    "install_professional_editor_patch_guard",
]

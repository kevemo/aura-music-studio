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


def _install_routes_once(routes) -> None:
    existing = {
        (
            getattr(current, "path", None),
            frozenset(getattr(current, "methods", set())),
            getattr(current, "endpoint", None),
        )
        for current in professional_editor_router.routes
    }
    for candidate in routes:
        signature = (
            getattr(candidate, "path", None),
            frozenset(getattr(candidate, "methods", set())),
            getattr(candidate, "endpoint", None),
        )
        if signature not in existing:
            professional_editor_router.routes.append(candidate)
            existing.add(signature)


def install_professional_editor_patch_guard() -> None:
    """Install guarded editor mutation, direct render/export and queued render surfaces.

    Heavy render modules are imported lazily so importing the package or this overlay remains
    lightweight for production-readiness commands. The PATCH guard is prepended because it
    must win route precedence; render routes are appended because they do not shadow legacy
    editor endpoints.
    """

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

    # Activate rendering only through the authenticated Professional Editor router so its
    # membership, Pro-feature and tenant-storage boundaries remain authoritative.
    from .professional_editor_render_api import router as render_router
    from .professional_editor_render_jobs import router as render_jobs_router

    _install_routes_once(render_router.routes)
    _install_routes_once(render_jobs_router.routes)


__all__ = [
    "router",
    "normalize_item_patch_sources",
    "install_professional_editor_patch_guard",
]

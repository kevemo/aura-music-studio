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
    """Return a copy of item patch changes with source_ref confined to the project."""
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
    """Install guarded editor, render, proxy, visual-effects, TV, cinema and transition surfaces."""
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

    from . import professional_editor_render_api as render_api_module
    from . import professional_editor_render_jobs as render_jobs_module
    from .cinema_production import router as cinema_production_router
    from .legacy_visual_effects import install_legacy_visual_effects
    from .professional_editor_render_api import router as render_router
    from .professional_editor_render_jobs import router as render_jobs_router
    from .professional_video_proxy import router as video_proxy_router
    from .professional_video_proxy_hardening import install_professional_video_proxy_hardening
    from .professional_video_transition_compositor import (
        TransitionAwareGroupedVideoCompositor,
        TransitionAwareUniversalVisualVideoCompositor,
    )
    from .professional_visual_transitions import router as visual_transition_router
    from .tv_production import router as tv_production_router
    from .visual_effect_catalogue import router as visual_effect_router
    from .visual_effect_catalogue_hardening import (
        install_visual_effect_catalogue_hardening,
        router as visual_effect_hardening_router,
    )

    # Recovered Aura visual modules are reference/provenance only. Register only rewritten,
    # repository-backed bounded processors in the canonical catalogue/compositor path.
    install_legacy_visual_effects()
    install_visual_effect_catalogue_hardening()
    install_professional_video_proxy_hardening()

    # Transition-aware renderers wrap the existing canonical production renderers. They only
    # consume strictly validated sequence transition resources and never accept caller-supplied
    # FFmpeg/filter strings. The original renderer classes remain the delegated implementation.
    render_api_module.UniversalVisualVideoCompositor = TransitionAwareUniversalVisualVideoCompositor
    render_jobs_module.GroupedUnifiedAdvancedVideoCompositor = TransitionAwareGroupedVideoCompositor

    _install_routes_once(render_router.routes)
    _install_routes_once(render_jobs_router.routes)
    # Editing proxies are preview-only project assets; final renderers retain the original item
    # source_ref, so proxy generation can never silently lower master/export quality.
    _install_routes_once(video_proxy_router.routes)
    # TV and cinema metadata mount into the existing project/editor route family. Their handoff
    # endpoints prepare delivery metadata only; Shared Skies transmission remains Chat 5 authority.
    _install_routes_once(tv_production_router.routes)
    _install_routes_once(cinema_production_router.routes)
    _install_routes_once(visual_transition_router.routes)
    # Guarded duplicate signatures are intentionally installed first. The final route-integrity
    # pass retains the hardened endpoint and removes the later legacy signature exactly once.
    _install_routes_once(visual_effect_hardening_router.routes)
    _install_routes_once(visual_effect_router.routes)


__all__ = [
    "router",
    "normalize_item_patch_sources",
    "install_professional_editor_patch_guard",
]

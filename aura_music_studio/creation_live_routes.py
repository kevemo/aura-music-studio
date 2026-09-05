from __future__ import annotations

from fastapi import APIRouter

from . import creation_live as cl
from .creation_live_authority import (
    authoritative_attach,
    authoritative_marker,
    authoritative_return,
)
from .creation_live_community import authoritative_community_panel


_PREFIX = "/creation-live"
_TAGS = ["Creation Studios Go Live & Create"]


def build_creation_live_router() -> APIRouter:
    """Build the canonical Chat 7 API surface from endpoint functions, not shared router state.

    The repository composes multiple FastAPI applications during validation and production
    bootstrap.  A module-level ``APIRouter.routes`` list is mutable process state, so it must not
    be the source of truth for whether Chat 7 can be mounted on a later application.  This factory
    creates a fresh router every time and registers the canonical endpoint functions directly.

    Consequential overlapping routes use the authority/community handlers by construction; the
    compatibility handlers in ``creation_live`` remain import-compatible but are never placed in
    the canonical production router.
    """
    router = APIRouter(prefix=_PREFIX, tags=_TAGS)

    router.add_api_route("/capabilities", cl.capabilities, methods=["GET"])
    router.add_api_route("/projects/{project_name}/sources", cl.sources, methods=["GET"])
    router.add_api_route(
        "/projects/{project_name}/sources/{source_adapter_id}",
        cl.source_status,
        methods=["GET"],
    )
    router.add_api_route(
        "/projects/{project_name}/sources/{source_adapter_id}/media",
        cl.source_media,
        methods=["GET"],
    )
    router.add_api_route(
        "/projects/{project_name}/sources/{source_adapter_id}/attach",
        authoritative_attach,
        methods=["POST"],
    )
    router.add_api_route(
        "/projects/{project_name}/sources/{source_adapter_id}/transition",
        cl.transition,
        methods=["POST"],
    )
    router.add_api_route(
        "/projects/{project_name}/sources/{source_adapter_id}/emergency-hide",
        cl.emergency_hide,
        methods=["POST"],
    )
    router.add_api_route(
        "/projects/{project_name}/sources/{source_adapter_id}/detach",
        cl.detach,
        methods=["POST"],
    )
    router.add_api_route("/shared-sky/broadcasts", cl.creator_broadcasts, methods=["GET"])
    router.add_api_route(
        "/projects/{project_name}/markers",
        authoritative_marker,
        methods=["POST"],
    )
    router.add_api_route(
        "/projects/{project_name}/returns",
        authoritative_return,
        methods=["POST"],
    )
    router.add_api_route(
        "/projects/{project_name}/community",
        authoritative_community_panel,
        methods=["GET"],
    )
    router.add_api_route(
        "/projects/{project_name}/aura-assistance",
        cl.aura_assistance,
        methods=["GET"],
    )
    router.add_api_route(
        "/ui.js",
        cl.live_ui,
        methods=["GET"],
        include_in_schema=False,
    )
    return router


__all__ = ["build_creation_live_router"]

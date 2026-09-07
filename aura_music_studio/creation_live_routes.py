from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from . import creation_live as cl
from .creation_live_authority import (
    authoritative_marker,
    authoritative_return,
)
from .creation_live_chat3_bridge import (
    authoritative_attach_with_chat3,
    install_creation_live_chat3_bridge,
)
from .creation_live_community import authoritative_community_panel
from .creation_live_transport_truth import (
    authoritative_source_status,
    install_creation_live_transport_truth,
)


_PREFIX = "/creation-live"
_TAGS = ["Creation Studios Go Live & Create"]

# Install exact Chat 3 Programme truth before endpoint objects are captured below. Chat 7 still
# owns only the creation-source adapter; Chat 3 continues to own Preview/Programme composition.
install_creation_live_chat3_bridge()

# Chat 7 hardening is installed before this module is imported by route_integrity. Apply the
# transport-truth overlay after that hardening so the browser script keeps both lifecycle cleanup
# and explicit registration/transport/Programme separation.
install_creation_live_transport_truth()

# path, endpoint, methods, include_in_schema
_ROUTE_SPECS: tuple[tuple[str, Callable[..., Any], tuple[str, ...], bool], ...] = (
    ("/capabilities", cl.capabilities, ("GET",), True),
    ("/projects/{project_name}/sources", cl.sources, ("GET",), True),
    ("/projects/{project_name}/sources/{source_adapter_id}", authoritative_source_status, ("GET",), True),
    ("/projects/{project_name}/sources/{source_adapter_id}/media", cl.source_media, ("GET",), True),
    (
        "/projects/{project_name}/sources/{source_adapter_id}/attach",
        authoritative_attach_with_chat3,
        ("POST",),
        True,
    ),
    (
        "/projects/{project_name}/sources/{source_adapter_id}/transition",
        cl.transition,
        ("POST",),
        True,
    ),
    (
        "/projects/{project_name}/sources/{source_adapter_id}/emergency-hide",
        cl.emergency_hide,
        ("POST",),
        True,
    ),
    ("/projects/{project_name}/sources/{source_adapter_id}/detach", cl.detach, ("POST",), True),
    ("/shared-sky/broadcasts", cl.creator_broadcasts, ("GET",), True),
    ("/projects/{project_name}/markers", authoritative_marker, ("POST",), True),
    ("/projects/{project_name}/returns", authoritative_return, ("POST",), True),
    ("/projects/{project_name}/community", authoritative_community_panel, ("GET",), True),
    ("/projects/{project_name}/aura-assistance", cl.aura_assistance, ("GET",), True),
    ("/ui.js", cl.live_ui, ("GET",), False),
)


def build_creation_live_router() -> APIRouter:
    """Build a standalone Chat 7 router from immutable endpoint specifications."""
    router = APIRouter(prefix=_PREFIX, tags=_TAGS)
    for path, endpoint, methods, include_in_schema in _ROUTE_SPECS:
        router.add_api_route(
            path,
            endpoint,
            methods=list(methods),
            include_in_schema=include_in_schema,
        )
    return router


def install_creation_live_api_routes(app: Any) -> None:
    """Bind Chat 7 directly to the canonical FastAPI app.

    This repository's production overlay composes some late routers through snapshot semantics.
    Direct ``app.add_api_route`` registration is already the established production pattern for
    routes that must remain reachable after every overlay has been applied. Registering the
    endpoint functions directly also avoids all dependence on mutable module-level APIRouter lists.
    """
    for path, endpoint, methods, include_in_schema in _ROUTE_SPECS:
        app.add_api_route(
            f"{_PREFIX}{path}",
            endpoint,
            methods=list(methods),
            tags=_TAGS,
            include_in_schema=include_in_schema,
        )


__all__ = ["build_creation_live_router", "install_creation_live_api_routes"]

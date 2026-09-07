from __future__ import annotations

from aura_music_studio.professional_editor_api import router as professional_editor_router
from aura_music_studio.professional_editor_render_api import router as render_router
from aura_music_studio.professional_editor_security_overlay import (
    install_professional_editor_patch_guard,
)


def _signature(route):
    return getattr(route, "path", None), frozenset(getattr(route, "methods", set()))


def test_render_routes_are_dormant_until_security_overlay_installs_them(monkeypatch):
    render_signatures = {_signature(route) for route in render_router.routes}
    original = list(professional_editor_router.routes)
    try:
        professional_editor_router.routes[:] = [
            route for route in original if _signature(route) not in render_signatures
        ]
        assert not render_signatures.intersection({_signature(route) for route in professional_editor_router.routes})

        install_professional_editor_patch_guard()

        installed = {_signature(route) for route in professional_editor_router.routes}
        assert render_signatures <= installed
    finally:
        professional_editor_router.routes[:] = original


def test_render_activation_is_idempotent():
    render_signatures = {_signature(route) for route in render_router.routes}
    original = list(professional_editor_router.routes)
    try:
        professional_editor_router.routes[:] = [
            route for route in original if _signature(route) not in render_signatures
        ]
        install_professional_editor_patch_guard()
        install_professional_editor_patch_guard()

        for signature in render_signatures:
            assert sum(_signature(route) == signature for route in professional_editor_router.routes) == 1
    finally:
        professional_editor_router.routes[:] = original


def test_activation_preserves_guarded_patch_precedence():
    original = list(professional_editor_router.routes)
    try:
        install_professional_editor_patch_guard()
        matching = [
            route
            for route in professional_editor_router.routes
            if getattr(route, "path", None) == "/creative/projects/{project_name}/editor/items/{item_id}"
            and "PATCH" in getattr(route, "methods", set())
        ]
        assert matching
        assert matching[0].endpoint.__name__ == "patch_item_source_guard"
    finally:
        professional_editor_router.routes[:] = original


def test_render_activation_exposes_only_existing_hardened_render_surface():
    render_signatures = {_signature(route) for route in render_router.routes}
    assert render_signatures == {
        (
            "/creative/projects/{project_name}/editor/sequences/{sequence_id}/render",
            frozenset({"POST"}),
        ),
        (
            "/creative/projects/{project_name}/editor/exports/{filename}",
            frozenset({"GET"}),
        ),
    }

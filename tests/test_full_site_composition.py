from __future__ import annotations

from collections import Counter

from fastapi.routing import APIRoute

import app as production_entrypoint


# These are representative production entrypoints for the major Command Center domains.
# The purpose is not to duplicate every feature test; it is to ensure the separate build lanes
# remain composed into one release application rather than existing only as isolated routers.
_REQUIRED_FULL_SITE_SURFACES = {
    ("GET", "/"),
    ("GET", "/dashboard"),
    ("GET", "/studio"),
    ("GET", "/video-studio"),
    ("GET", "/image-designer"),
    ("GET", "/creative/library"),
    ("GET", "/game-creation"),
    ("GET", "/aura"),
    ("GET", "/aura-intelligence"),
    ("GET", "/aura/self-host/status"),
    ("GET", "/command-center"),
    ("GET", "/owner"),
    ("POST", "/billing/stripe/checkout/marketplace"),
    ("POST", "/billing/stripe/webhook"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
}


def _production_surface_counts() -> Counter[tuple[str, str]]:
    mounted: Counter[tuple[str, str]] = Counter()
    for route in production_entrypoint.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            mounted[(method.upper(), route.path)] += 1
    return mounted


def test_major_full_site_surfaces_are_composed_exactly_once():
    mounted = _production_surface_counts()
    missing = sorted(surface for surface in _REQUIRED_FULL_SITE_SURFACES if mounted[surface] == 0)
    duplicated = sorted(
        (method, path, mounted[(method, path)])
        for method, path in _REQUIRED_FULL_SITE_SURFACES
        if mounted[(method, path)] > 1
    )

    assert not missing, (
        "A major Command Center surface exists in the build plan but is not mounted on the "
        f"production application: {missing}"
    )
    assert not duplicated, (
        "A major Command Center surface is mounted more than once in the production application: "
        f"{duplicated}"
    )


def test_self_host_control_is_part_of_the_real_release_app():
    mounted = _production_surface_counts()
    assert mounted[("GET", "/aura/self-host/status")] == 1

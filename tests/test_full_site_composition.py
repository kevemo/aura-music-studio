from __future__ import annotations

import app as production_entrypoint


# Hidden/private HTML surfaces are validated through Starlette's named route resolver. This is the
# authoritative routing contract for the production app and remains valid when compatibility
# overlays intentionally stack more than one handler on the same URL.
_REQUIRED_NAMED_SURFACES = {
    "member_dashboard": "/dashboard",
    "studio": "/studio",
    "video_studio_entry": "/video-studio",
    "image_designer_entry": "/image-designer",
    "creative_library_page": "/creative/library",
    "game_creation_portal": "/game-creation",
    "aura_realtime_page": "/aura-intelligence",
    "esp_gateway": "/command-center",
    "owner_login": "/owner",
}

# Security-sensitive/service endpoints are schema-visible and are validated from the OpenAPI graph
# produced by the real assembled application. This catches routers that exist in source but were
# never mounted at the production entrypoint.
_REQUIRED_OPENAPI_OPERATIONS = {
    ("get", "/aura/self-host/status"),
    ("post", "/billing/stripe/checkout/marketplace"),
    ("post", "/billing/stripe/webhook"),
    ("get", "/health/live"),
    ("get", "/health/ready"),
}


def test_major_private_and_ui_surfaces_resolve_on_real_release_app():
    missing: list[tuple[str, str, str]] = []
    for route_name, expected_path in _REQUIRED_NAMED_SURFACES.items():
        try:
            resolved = str(production_entrypoint.app.url_path_for(route_name))
        except Exception as exc:  # pragma: no cover - assertion reports exact missing route
            missing.append((route_name, expected_path, type(exc).__name__))
            continue
        if resolved != expected_path:
            missing.append((route_name, expected_path, resolved))

    assert not missing, (
        "A major Command Center UI/private surface is not composed into the production app: "
        f"{missing}"
    )


def test_security_sensitive_full_site_apis_are_mounted_in_openapi_graph():
    schema = production_entrypoint.app.openapi()
    paths = schema.get("paths", {})
    missing = sorted(
        (method.upper(), path)
        for method, path in _REQUIRED_OPENAPI_OPERATIONS
        if method not in paths.get(path, {})
    )
    assert not missing, (
        "A security-sensitive Command Center API exists in source but is not mounted on the real "
        f"release application: {missing}"
    )


def test_self_host_control_is_part_of_the_real_release_app():
    schema = production_entrypoint.app.openapi()
    assert "get" in schema.get("paths", {}).get("/aura/self-host/status", {})

from __future__ import annotations

from fastapi.testclient import TestClient

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

# These product workspaces are exercised through the real ASGI application rather than by
# inspecting FastAPI route object classes. Late composition/middleware adapters are allowed to
# change the internal route representation, but the actual production URL must still exist and
# must not crash. Authentication redirects/401/403 are valid evidence that the lane is mounted.
_REQUIRED_REQUEST_SURFACES = (
    "/creative-house",
    "/aura-sec",
    "/live-overlay-studio",
    "/command-center/social",
    "/production-suite",
    "/daw",
    "/voice-house/composition-probe",
)

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


def test_cross_product_workspaces_answer_through_one_release_application():
    failures: dict[str, int] = {}
    with TestClient(production_entrypoint.app, raise_server_exceptions=False) as client:
        for path in _REQUIRED_REQUEST_SURFACES:
            response = client.get(path, follow_redirects=False)
            if response.status_code == 404 or response.status_code >= 500:
                failures[path] = response.status_code

    assert not failures, (
        "A major product lane is missing or crashing on the one production Command Center "
        f"application: {failures}"
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

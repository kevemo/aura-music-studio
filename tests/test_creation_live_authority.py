from __future__ import annotations

from fastapi import FastAPI

from aura_music_studio.creation_live_authority import _processing_state
from aura_music_studio.route_integrity import deduplicate_http_routes, duplicate_http_signatures


def test_consequential_creation_live_routes_use_authority_handlers():
    app = FastAPI()
    deduplicate_http_routes(app)

    expected = {
        "/creation-live/projects/{project_name}/sources/{source_adapter_id}/attach",
        "/creation-live/projects/{project_name}/markers",
        "/creation-live/projects/{project_name}/returns",
    }
    for path in expected:
        routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) == path and "POST" in (getattr(route, "methods", set()) or set())
        ]
        assert len(routes) == 1
        assert routes[0].endpoint.__module__ == "aura_music_studio.creation_live_authority"

    assert duplicate_http_signatures(app.router.routes) == {}


def test_route_reconciliation_stays_idempotent_with_authority_handlers():
    app = FastAPI()
    deduplicate_http_routes(app)
    first = [
        (getattr(route, "path", None), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.router.routes
        if str(getattr(route, "path", "")).startswith("/creation-live")
    ]
    deduplicate_http_routes(app)
    second = [
        (getattr(route, "path", None), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in app.router.routes
        if str(getattr(route, "path", "")).startswith("/creation-live")
    ]
    assert second == first
    assert len([path for path, _ in second if path == "/creation-live/capabilities"]) == 1


def test_recording_processing_state_uses_authoritative_asset_readiness():
    ready = {
        "available": True,
        "recording": {"state": "ready", "asset_id": "asset_123"},
    }
    no_asset = {
        "available": True,
        "recording": {"state": "ready", "asset_id": None},
    }
    incomplete = {
        "available": True,
        "recording": {"state": "incomplete", "asset_id": None},
    }
    recovered = {
        "available": True,
        "recording": {"state": "recovered", "asset_id": "asset_456"},
    }
    recording = {
        "available": True,
        "recording": {"state": "recording", "asset_id": None},
    }

    assert _processing_state(ready, "processing") == "ready"
    assert _processing_state(no_asset, "ready") == "processing"
    assert _processing_state(incomplete, "ready") == "incomplete"
    assert _processing_state(recovered, "processing") == "recovered"
    assert _processing_state(recording, "ready") == "processing"


def test_unmerged_recording_authority_never_upgrades_client_state():
    compatibility = {"available": False, "recording": None}
    assert _processing_state(compatibility, "processing") == "processing"
    assert _processing_state(compatibility, "failed") == "failed"

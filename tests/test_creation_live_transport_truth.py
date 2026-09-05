from __future__ import annotations

from fastapi import FastAPI

from aura_music_studio import creation_live as cl
from aura_music_studio import creation_live_authority as authority
from aura_music_studio.creation_live_transport_truth import (
    _safe_status_snapshot,
    install_creation_live_transport_truth,
    safe_chat2_preflight,
    safe_preflight_payload,
)
from aura_music_studio.route_integrity import deduplicate_http_routes


def test_preflight_projection_whitelists_safe_fields_only():
    raw = {
        "ready": False,
        "blocking_errors": [
            {
                "code": "source_required",
                "scope": "source",
                "message": "Register a programme source before go-live",
                "endpoint": "rtmps://secret.example/live/key",
                "provider_payload": {"token": "do-not-leak"},
                "trace_id": "trace_private",
            }
        ],
        "warnings": [
            {
                "code": "signed_ingest_contract_pending_merge",
                "scope": "ingest",
                "message": "Compatibility warning",
                "secret": "nope",
            }
        ],
        "destinations": [{"provider_id": "provider-private-detail"}],
        "internal_playback": {"authorization": {"token": "secret-token"}},
        "correlation_id": "corr_public_safe",
        "trace_id": "trace_private",
    }

    projected = safe_preflight_payload(raw)

    assert projected["available"] is True
    assert projected["ready"] is False
    assert projected["state"] == "blocked"
    assert projected["blocking_errors"] == [
        {
            "code": "source_required",
            "scope": "source",
            "message": "Register a programme source before go-live",
        }
    ]
    assert projected["warnings"] == [
        {
            "code": "signed_ingest_contract_pending_merge",
            "scope": "ingest",
            "message": "Compatibility warning",
        }
    ]
    assert projected["correlation_id"] == "corr_public_safe"
    serialized = repr(projected)
    for forbidden in ("trace_private", "secret-token", "do-not-leak", "secret.example", "provider-private-detail"):
        assert forbidden not in serialized


def test_status_snapshot_does_not_expose_transport_source_id_or_trace_id():
    status = {
        "session": {
            "state": "live",
            "source_id": "src_exact_private_identifier",
            "trace_id": "trace_private",
            "health_state": "healthy",
            "last_reason_code": "live",
            "validation": {
                "ready": True,
                "blocking_errors": [],
                "warnings": [],
                "trace_id": "trace_validation_private",
            },
        },
        "relay": {"secret": "not-for-chat7"},
    }

    projected = _safe_status_snapshot(
        status,
        registered_source_id="src_exact_private_identifier",
    )

    assert projected["ready"] is True
    assert projected["state"] == "ready"
    assert projected["transport_state"] == "live"
    assert projected["registered_source"] is True
    assert projected["transport_source_selected"] is True
    assert projected["transport_uses_this_source"] is True
    assert projected["active_transport_session"] is True
    serialized = repr(projected)
    assert "src_exact_private_identifier" not in serialized
    assert "trace_private" not in serialized
    assert "trace_validation_private" not in serialized
    assert "not-for-chat7" not in serialized


def test_transport_ready_never_changes_programme_truth_in_ui():
    install_creation_live_transport_truth()
    script = cl.LIVE_UI_SCRIPT

    assert "Transport preflight:" in script
    assert "Registration:" in script
    assert "transport_uses_this_source" in script
    assert "NOT CONFIRMED ON AIR" in script
    assert "pf.ready===true" in script
    assert "Programme remains NOT CONFIRMED ON AIR" in script


def test_source_status_route_uses_transport_truth_handler_and_attach_keeps_authority():
    app = FastAPI()
    deduplicate_http_routes(app)

    status_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/creation-live/projects/{project_name}/sources/{source_adapter_id}"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    attach_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/creation-live/projects/{project_name}/sources/{source_adapter_id}/attach"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]

    assert len(status_routes) == 1
    assert status_routes[0].endpoint.__module__ == "aura_music_studio.creation_live_transport_truth"
    assert len(attach_routes) == 1
    assert attach_routes[0].endpoint.__module__ == "aura_music_studio.creation_live_authority"
    assert authority._safe_chat2_preflight is safe_chat2_preflight

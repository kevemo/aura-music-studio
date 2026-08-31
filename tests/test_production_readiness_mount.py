from __future__ import annotations

import app as production_entrypoint


def test_production_entrypoint_exposes_release_health_surfaces():
    schema = production_entrypoint.app.openapi()
    paths = schema.get("paths", {})

    assert "/health/live" in paths
    assert "get" in paths["/health/live"]
    assert "/health/ready" in paths
    assert "get" in paths["/health/ready"]

    # Metrics remain intentionally authenticated and excluded from public OpenAPI discovery.
    assert "/internal/metrics" not in paths

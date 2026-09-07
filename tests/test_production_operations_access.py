from __future__ import annotations

from aura_music_studio.access_control import PUBLIC_EXACT
import aura_music_studio.production_readiness as production_readiness


def test_operational_probe_paths_bypass_membership_envelope():
    # Importing the operations module registers only these infrastructure probe paths as
    # middleware-public so container/orchestrator checks work before any member signs in.
    assert production_readiness.router is not None
    assert {"/health/live", "/health/ready", "/internal/metrics"}.issubset(PUBLIC_EXACT)


def test_metrics_route_remains_independently_token_protected(monkeypatch):
    monkeypatch.setenv("AURA_MONITORING_ENABLED", "true")
    monkeypatch.setenv("AURA_MONITORING_TOKEN", "monitoring-secret-0123456789")
    assert production_readiness._monitoring_authorized(None) == (False, "bad_token")
    assert production_readiness._monitoring_authorized("wrong-monitoring-secret") == (False, "bad_token")
    assert production_readiness._monitoring_authorized("monitoring-secret-0123456789") == (True, "authorized")

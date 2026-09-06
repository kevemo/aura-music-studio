from __future__ import annotations

import aura_music_studio._production_readiness_impl as readiness_impl
from aura_music_studio.production_readiness import build_readiness_report


def test_config_only_validation_never_claims_serving_readiness():
    report = build_readiness_report({"AURA_DEPLOYMENT_ENV": "development"})

    assert report["ok"] is True
    assert report["configuration_ready"] is True
    assert report["runtime_probes_performed"] is False
    assert report["serving_ready"] is False
    assert "runtime_dependencies" in report["serving_blocking_categories"]
    assert report["production_ready"] is False


def test_verified_runtime_probe_can_make_nonproduction_serving_ready(monkeypatch):
    monkeypatch.setattr(
        readiness_impl,
        "probe_runtime_storage",
        lambda _env: {
            "verified": True,
            "database": {"state": "healthy", "connectivity_check": "ok"},
            "project_storage": {"state": "healthy"},
            "backup_storage": {"state": "healthy"},
            "external_provider_probes_performed": False,
            "destructive_writes_performed": False,
        },
    )

    report = build_readiness_report(
        {"AURA_DEPLOYMENT_ENV": "development"},
        perform_runtime_probes=True,
    )

    assert report["ok"] is True
    assert report["configuration_ready"] is True
    assert report["runtime_probes_performed"] is True
    assert report["serving_ready"] is True
    assert "runtime_dependencies" not in report["serving_blocking_categories"]
    assert report["production_ready"] is False

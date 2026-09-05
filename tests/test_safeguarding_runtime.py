from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio import safeguarding_runtime as runtime


def test_aura_safeguarding_core_is_local_and_fail_closed():
    status = runtime.build_safeguarding_runtime_status()
    assert status["self_hosted_by_aura"] is True
    assert status["core_ready"] is True
    assert status["core_execution_offline_capable"] is True
    assert status["remote_moderation_api_required"] is False
    assert status["external_services_are_not_core_execution_dependencies"] is True
    assert status["local_safety_blocks_are_authoritative"] is True
    assert status["external_evidence_can_add_restrictions_but_not_disable_local_blocks"] is True
    assert status["legal_certification"] is False
    assert status["legal_advice"] is False
    assert status["failures"] == []


def test_every_critical_safeguard_remains_self_hosted():
    assert runtime.CORE_COMPONENTS
    for component in runtime.CORE_COMPONENTS:
        if not component.critical:
            continue
        assert component.self_hosted is True
        assert component.remote_dependency_required is False
        assert component.external_can_override_local_block is False
        assert component.execution_surface.startswith("local_")


def test_external_evidence_never_becomes_the_core_or_weakens_aura():
    boundaries = {item["boundary_id"]: item for item in runtime.EXTERNAL_EVIDENCE_BOUNDARIES}
    assert {"qualified_legal_review", "platform_side_enforcement", "external_similarity_or_catalog_review"} <= set(boundaries)
    assert boundaries["qualified_legal_review"]["may_be_required"] is True
    assert boundaries["platform_side_enforcement"]["may_be_required"] is True
    for item in boundaries.values():
        assert item["core_execution_dependency"] is False
        assert item["can_disable_local_safety_block"] is False


def test_remote_only_critical_safeguard_fails_release_gate():
    unsafe = runtime.SafeguardingComponent(
        component_id="remote_only_test",
        purpose="Regression fixture",
        execution_surface="remote_api",
        self_hosted=False,
        remote_dependency_required=True,
    )
    failures = runtime.self_hosted_core_failures((unsafe,))
    assert "remote_only_test:critical_component_not_self_hosted" in failures
    assert "remote_only_test:remote_dependency_required" in failures
    with pytest.raises(RuntimeError, match="not self-hosted/fail-closed"):
        runtime.assert_self_hosted_core_ready((unsafe,))


def test_external_override_of_local_block_fails_release_gate():
    unsafe = runtime.SafeguardingComponent(
        component_id="override_test",
        purpose="Regression fixture",
        execution_surface="local_python",
        external_can_override_local_block=True,
    )
    assert runtime.self_hosted_core_failures((unsafe,)) == (
        "override_test:external_override_enabled",
    )
    with pytest.raises(RuntimeError, match="external_override_enabled"):
        runtime.assert_self_hosted_core_ready((unsafe,))


def test_owner_runtime_route_is_private_and_fail_closed(monkeypatch):
    app = FastAPI()
    app.include_router(runtime.owner_router)
    client = TestClient(app)

    denied = client.get("/owner/safeguarding/runtime")
    assert denied.status_code == 403

    monkeypatch.setattr(runtime, "owner_authorized", lambda request: True)
    response = client.get("/owner/safeguarding/runtime")
    assert response.status_code == 200
    assert response.json()["core_ready"] is True
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "/owner/safeguarding/runtime" not in app.openapi()["paths"]


def test_runtime_route_is_mounted_in_governance_integration_via_dispatch():
    from aura_music_studio.creative_version_autopromotion import router as integration_router

    app = FastAPI()
    app.include_router(integration_router)
    client = TestClient(app)

    # Hidden owner governance routes do not appear in public OpenAPI. Prove the route is
    # genuinely composed by ASGI dispatch: the existing owner gate must answer 403, not 404.
    assert "/owner/safeguarding/runtime" not in app.openapi()["paths"]
    assert client.get("/owner/safeguarding/runtime").status_code == 403


def test_creation_safety_import_exposes_same_self_hosted_invariant():
    from aura_music_studio import content_safety

    # content_safety imports and executes the runtime assertion during normal app assembly.
    assert content_safety.assert_self_hosted_core_ready is runtime.assert_self_hosted_core_ready
    content_safety.assert_self_hosted_core_ready()

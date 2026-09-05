from __future__ import annotations

from aura_music_studio.esp_product_workflows_portal import router


def test_chat9_portal_exposes_creator_onboarding_and_agent_crm_routes():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/onboarding" in paths
    assert "/command-center/agent/leads" in paths


def test_chat9_portal_marks_workflow_pages_outside_openapi_schema():
    for route in router.routes:
        if getattr(route, "path", None) in {"/command-center/onboarding", "/command-center/agent/leads"}:
            assert route.include_in_schema is False

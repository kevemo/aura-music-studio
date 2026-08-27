from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from aura_music_studio.plans import PLANS
from aura_music_studio.professional_editor_workspace import (
    professional_editor_workspace,
    router as workspace_router,
)


def _request(plan_id: str) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/creative/editor-workspace", "headers": []})
    request.state.member = SimpleNamespace(plan=PLANS[plan_id], user_id="member-test", user={"display_name": "Test Member"})
    return request


def _html(plan_id: str, project: str = "") -> str:
    response = professional_editor_workspace(_request(plan_id), project=project)
    return response.body.decode("utf-8")


def test_workspace_requires_basic_timeline_entitlement():
    with pytest.raises(HTTPException) as exc:
        professional_editor_workspace(_request("free"))
    assert exc.value.status_code == 403
    assert "Basic" in str(exc.value.detail)


def test_basic_workspace_is_real_editor_but_pro_controls_fail_closed():
    html = _html("base", "video-project")
    assert "Professional non-destructive editor" in html
    assert 'data-pro="false"' in html
    assert "Source media is never overwritten" in html
    assert "Final compositor/export rendering is not labelled complete here" in html
    assert "mutate('/undo')" in html
    assert "/editor${s}" in html
    assert "/ripple-delete" in html
    assert "/split" in html
    assert "A/B editor branching requires Pro" in html


def test_pro_workspace_enables_advanced_editor_ui_capability_flag():
    html = _html("pro", "image-project")
    assert 'data-pro="true"' in html
    assert "Add rectangle mask" in html
    assert "Add colour effect" in html
    assert "A/B Branch" in html
    assert "/masks" in html
    assert "/effects" in html


def test_workspace_escapes_project_query_before_embedding():
    payload = '\"><script>alert(1)</script>'
    html = _html("base", payload)
    assert payload not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_workspace_route_is_mounted_on_its_router():
    paths = {route.path for route in workspace_router.routes if hasattr(route, "path")}
    assert "/creative/editor-workspace" in paths

from __future__ import annotations

from aura_music_studio import esp_support_casework_portal as portal


def _html(role: str) -> str:
    return portal._page(role).body.decode("utf-8")


def test_creator_casework_portal_never_renders_staff_only_controls():
    html = _html("creator")
    assert "Support Casework Desk" in html
    assert "Creator-visible reply" in html
    assert "Internal staff note" not in html
    assert "Internal staff only" not in html
    assert "Staff case controls" not in html
    assert "/command-center/api/support" in html


def test_agent_casework_portal_renders_assigned_queue_and_staff_controls():
    html = _html("agent")
    assert "Assigned Creator queue + staff controls" in html
    assert "Internal staff note" in html
    assert "Internal staff only" in html
    assert "Staff case controls" in html
    assert "/command-center/api/agent/support/cases" in html


def test_owner_casework_portal_uses_owner_queue():
    html = _html("owner")
    assert "Owner queue + staff controls" in html
    assert "/owner/cases" in html


def test_casework_portal_route_is_mounted_on_its_router():
    paths = {getattr(route, "path", None) for route in portal.router.routes}
    assert "/command-center/support/casework" in paths

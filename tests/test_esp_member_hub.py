from __future__ import annotations

from aura_music_studio.esp_member_hub import catalog_for, router


def _ids(data: dict) -> set[str]:
    return {row["id"] for row in data["modules"]}


def test_creator_catalog_never_exposes_agent_only_modules():
    data = catalog_for(
        {"status": "active", "roles": "creator"},
        {"niche": "music", "sub_niche": "singer"},
    )
    ids = _ids(data)
    assert "my-plan" in ids
    assert "creator-progress" in ids
    assert "social" in ids
    assert "creator-discovery" not in ids
    assert "assigned-roster" not in ids
    assert "health-queue" not in ids
    assert "success-ops" not in ids
    assert data["private_esp_only"] is True
    assert data["creative_subscription_grants_esp"] is False
    assert data["niche"] == "music"


def test_agent_catalog_has_discovery_but_not_creator_my_plan():
    data = catalog_for({"status": "active", "roles": "agent"}, {"niche": "business"})
    ids = _ids(data)
    assert "creator-discovery" in ids
    assert "recruitment-funnel" in ids
    assert "assigned-roster" in ids
    assert "success-ops" in ids
    assert "my-plan" not in ids
    assert "creator-progress" not in ids


def test_creator_plus_agent_catalog_combines_both_views():
    data = catalog_for({"status": "active", "roles": "both"}, {"niche": "gaming"})
    ids = _ids(data)
    assert "my-plan" in ids
    assert "creator-progress" in ids
    assert "creator-discovery" in ids
    assert "assigned-roster" in ids
    assert "social" in ids


def test_owner_catalog_can_see_all_operational_groups():
    data = catalog_for({"status": "owner", "roles": "owner"}, {"niche": "other"})
    ids = _ids(data)
    assert "owner-focus" in ids
    assert "creator-discovery" in ids
    assert "my-plan" in ids
    assert "support" in ids
    assert "brands" in ids


def test_member_hub_router_only_registers_private_command_center_paths():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/member-hub" in paths
    assert "/command-center/api/member-hub/catalog" in paths
    assert "/member-hub" not in paths
    assert "/api/member-hub/catalog" not in paths

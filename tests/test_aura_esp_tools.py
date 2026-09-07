from types import SimpleNamespace

import pytest

from aura_music_studio import aura_esp_tools
from aura_music_studio.aura_agent_tools import AuraToolRegistry, ToolCall


class FakeEspStore:
    rows = {}

    def __init__(self, *args, **kwargs):
        pass

    def membership(self, user_id: str):
        row = self.rows.get(user_id)
        return dict(row) if row else None


def _member(user_id="member-1"):
    return SimpleNamespace(user_id=user_id)


def _registry(user_id="member-1"):
    return AuraToolRegistry(member=_member(user_id), pinned_project=None, web_enabled=False, tools_enabled=True)


def test_esp_tool_is_not_discoverable_without_active_esp_access(monkeypatch):
    FakeEspStore.rows = {}
    monkeypatch.setattr(aura_esp_tools, "EspStore", FakeEspStore)
    aura_esp_tools.install_aura_esp_tools()

    names = {row["name"] for row in _registry().specs()}
    assert "inspect_esp_workspace" not in names

    with pytest.raises(PermissionError):
        _registry().execute(ToolCall(name="inspect_esp_workspace"), latest_user_message="show my esp workspace")


def test_creator_receives_only_self_scoped_role_context(monkeypatch):
    FakeEspStore.rows = {
        "creator-1": {
            "user_id": "creator-1",
            "status": "active",
            "roles": "creator",
            "tiktok_handle": "creator_handle",
            "region": "UK+",
        }
    }
    monkeypatch.setattr(aura_esp_tools, "EspStore", FakeEspStore)
    aura_esp_tools.install_aura_esp_tools()

    registry = _registry("creator-1")
    assert "inspect_esp_workspace" in {row["name"] for row in registry.specs()}
    result = registry.execute(ToolCall(name="inspect_esp_workspace"), latest_user_message="show my esp workspace")

    assert result["esp_role"] == "creator"
    assert result["can_use_creator_workspace"] is True
    assert result["can_use_agent_workspace"] is False
    assert result["can_use_owner_workspace"] is False
    assert result["authority_changed"] is False
    assert all("url" not in resource for resource in result["resources"])
    assert all(resource["id"] != "agent-apprentice" for resource in result["resources"])


def test_agent_and_owner_capabilities_remain_role_bounded(monkeypatch):
    FakeEspStore.rows = {
        "agent-1": {"user_id": "agent-1", "status": "active", "roles": "agent", "region": "US"},
        "owner-1": {"user_id": "owner-1", "status": "owner", "roles": "", "region": "global"},
    }
    monkeypatch.setattr(aura_esp_tools, "EspStore", FakeEspStore)
    aura_esp_tools.install_aura_esp_tools()

    agent = _registry("agent-1").execute(ToolCall(name="inspect_esp_workspace"), latest_user_message="inspect my esp access")
    owner = _registry("owner-1").execute(ToolCall(name="inspect_esp_workspace"), latest_user_message="inspect my esp access")

    assert agent["can_use_creator_workspace"] is False
    assert agent["can_use_agent_workspace"] is True
    assert agent["can_use_owner_workspace"] is False
    assert any(resource["id"] == "agent-apprentice" for resource in agent["resources"])

    assert owner["esp_role"] == "owner"
    assert owner["can_use_creator_workspace"] is True
    assert owner["can_use_agent_workspace"] is True
    assert owner["can_use_owner_workspace"] is True


def test_tools_disabled_still_blocks_esp_tool(monkeypatch):
    FakeEspStore.rows = {"creator-1": {"user_id": "creator-1", "status": "active", "roles": "creator"}}
    monkeypatch.setattr(aura_esp_tools, "EspStore", FakeEspStore)
    aura_esp_tools.install_aura_esp_tools()
    registry = AuraToolRegistry(member=_member("creator-1"), pinned_project=None, web_enabled=False, tools_enabled=False)

    assert "inspect_esp_workspace" not in {row["name"] for row in registry.specs()}
    with pytest.raises(PermissionError):
        registry.execute(ToolCall(name="inspect_esp_workspace"), latest_user_message="show my esp workspace")

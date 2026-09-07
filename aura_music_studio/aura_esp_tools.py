from __future__ import annotations

from . import aura_agent_tools as tools
from .esp_command_center import EspStore, RESOURCE_CATALOG

_INSTALLED = False
_SPEC = tools.ToolSpec(
    "inspect_esp_workspace",
    "Read the signed-in member's own ESP Creator Network access, role and role-eligible resource catalogue. This never grants ESP access or exposes another member's records.",
    {},
)


def _member_id(member) -> str:
    value = getattr(member, "user_id", None)
    if not value and isinstance(member, dict):
        value = member.get("id") or member.get("user_id")
    return str(value or "").strip()


def _membership(member) -> dict | None:
    user_id = _member_id(member)
    if not user_id:
        return None
    return EspStore().membership(user_id)


def _eligible(membership: dict | None) -> bool:
    return bool(membership and membership.get("status") in {"active", "owner"})


def _role(membership: dict) -> str:
    if membership.get("status") == "owner":
        return "owner"
    role = str(membership.get("roles") or "").strip().lower()
    return role if role in {"creator", "agent", "both"} else ""


def _workspace_snapshot(member) -> dict:
    membership = _membership(member)
    if not _eligible(membership):
        raise PermissionError("ESP workspace access is available only to an active ESP Creator, Agent, Both-role member, or ESP Owner")
    assert membership is not None
    role = _role(membership)
    allowed = {role}
    if role == "both":
        allowed.update({"creator", "agent"})
    if role == "owner":
        allowed.update({"creator", "agent", "both", "owner"})
    resources = [
        {
            "id": resource_id,
            "title": row["title"],
            "category": row["category"],
            "description": row["description"],
        }
        for resource_id, row in RESOURCE_CATALOG.items()
        if set(row.get("roles") or ()).intersection(allowed)
    ]
    return {
        "esp_status": membership.get("status"),
        "esp_role": role,
        "region": membership.get("region"),
        "tiktok_handle": membership.get("tiktok_handle"),
        "resources": resources,
        "can_use_creator_workspace": role in {"creator", "both", "owner"},
        "can_use_agent_workspace": role in {"agent", "both", "owner"},
        "can_use_owner_workspace": role == "owner",
        "authority_changed": False,
    }


def install_aura_esp_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    tools._SPEC_BY_NAME[_SPEC.name] = _SPEC
    original_specs = tools.AuraToolRegistry.specs
    original_execute = tools.AuraToolRegistry.execute

    def specs(self):
        rows = list(original_specs(self))
        if self.tools_enabled and _eligible(_membership(self.member)) and not any(row.get("name") == _SPEC.name for row in rows):
            rows.append(_SPEC.public())
        return rows

    def execute(self, call, *, latest_user_message: str):
        if call.name == _SPEC.name:
            if not self.tools_enabled:
                raise PermissionError("Aura tools are disabled for this conversation")
            return _workspace_snapshot(self.member)
        return original_execute(self, call, latest_user_message=latest_user_message)

    tools.AuraToolRegistry.specs = specs
    tools.AuraToolRegistry.execute = execute

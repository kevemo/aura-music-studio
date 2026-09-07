from __future__ import annotations

import pytest

from aura_music_studio import esp_product_workflows_lead_hardening as _lead_hardening  # noqa: F401
from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_product_workflows import Chat9WorkflowStore, LeadCreate


def _active_user(accounts: AccountStore, email: str, display: str) -> dict:
    signup = accounts.signup(email, display, "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")


def _esp_role(esp: EspStore, user_id: str, role: str, *, status: str = "active") -> None:
    with esp._connect() as con:
        con.execute(
            """INSERT INTO esp_memberships(user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
               VALUES (?,?,?,?,?,datetime('now'),'test-owner',datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,roles=excluded.roles,
                 approved_at=excluded.approved_at,approved_by=excluded.approved_by,updated_at=excluded.updated_at""",
            (user_id, status, role, "", "UK+"),
        )


def _store(tmp_path):
    accounts = AccountStore(tmp_path / "chat9-lead-hardening.sqlite3")
    esp = EspStore(accounts)
    return accounts, esp, Chat9WorkflowStore(esp)


def _lead(handle: str) -> LeadCreate:
    return LeadCreate(platform="TikTok", handle=handle, region="UK+", niche="music", source="test")


def test_lead_assignment_rejects_existing_non_agent_and_revoked_agent(tmp_path):
    accounts, esp, workflows = _store(tmp_path)
    actor = _active_user(accounts, "actor@example.com", "Active Agent")
    creator = _active_user(accounts, "creator@example.com", "Creator")
    revoked = _active_user(accounts, "revoked@example.com", "Revoked Agent")
    _esp_role(esp, actor["id"], "agent")
    _esp_role(esp, creator["id"], "creator")
    _esp_role(esp, revoked["id"], "agent", status="revoked")

    with pytest.raises(ValueError, match="active ESP Agent/Both/Owner authority"):
        workflows.create_lead(
            _lead("creator.is.not.agent"),
            actor_user_id=actor["id"],
            assigned_agent_user_id=creator["id"],
        )

    with pytest.raises(ValueError, match="active ESP Agent/Both/Owner authority"):
        workflows.create_lead(
            _lead("revoked.agent"),
            actor_user_id=actor["id"],
            assigned_agent_user_id=revoked["id"],
        )


def test_lead_assignment_accepts_active_agent_both_and_owner(tmp_path):
    accounts, esp, workflows = _store(tmp_path)
    agent = _active_user(accounts, "agent@example.com", "Agent")
    both = _active_user(accounts, "both@example.com", "Both")
    owner = _active_user(accounts, "owner@example.com", "Owner")
    _esp_role(esp, agent["id"], "agent")
    _esp_role(esp, both["id"], "both")
    _esp_role(esp, owner["id"], "", status="owner")

    agent_lead = workflows.create_lead(
        _lead("active.agent"), actor_user_id=agent["id"], assigned_agent_user_id=agent["id"]
    )
    both_lead = workflows.create_lead(
        _lead("active.both"), actor_user_id=both["id"], assigned_agent_user_id=both["id"]
    )
    owner_lead = workflows.create_lead(
        _lead("active.owner"), actor_user_id=owner["id"], assigned_agent_user_id=owner["id"]
    )

    assert agent_lead["assigned_agent_user_id"] == agent["id"]
    assert both_lead["assigned_agent_user_id"] == both["id"]
    assert owner_lead["assigned_agent_user_id"] == owner["id"]


def test_lead_assignment_rechecks_current_role_on_every_write(tmp_path):
    accounts, esp, workflows = _store(tmp_path)
    agent = _active_user(accounts, "dynamic@example.com", "Dynamic Agent")
    _esp_role(esp, agent["id"], "agent")

    created = workflows.create_lead(
        _lead("before.revocation"), actor_user_id=agent["id"], assigned_agent_user_id=agent["id"]
    )
    assert created["assigned_agent_user_id"] == agent["id"]

    _esp_role(esp, agent["id"], "agent", status="revoked")
    with pytest.raises(ValueError, match="active ESP Agent/Both/Owner authority"):
        workflows.create_lead(
            _lead("after.revocation"), actor_user_id=agent["id"], assigned_agent_user_id=agent["id"]
        )

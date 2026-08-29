from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_roster import AgentRosterStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.esp_niche import EspNicheStore


def _active(accounts: AccountStore, esp: EspStore, email: str, role: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], role, email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", role, "Owner")
    return user


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    # The production app initializes niche profiles before the Level Up assignment reader is
    # used. Mirror that dependency in isolated test databases so for_agent() exercises the real
    # shared reader rather than failing on an intentionally omitted supporting table.
    EspNicheStore(esp)
    assignment_store = EspAgentAssignmentStore(esp)
    AgentRosterStore(esp, assignment_store)  # installs cross-consumer role-boundary triggers
    return accounts, esp, assignment_store


def _assignment_row(store: EspAgentAssignmentStore, agent_user_id: str, creator_user_id: str):
    with store._connect() as con:
        return con.execute(
            """SELECT status,revoked_by,revoked_at
               FROM esp_agent_creator_assignments
               WHERE agent_user_id=? AND creator_user_id=?""",
            (agent_user_id, creator_user_id),
        ).fetchone()


def test_creator_role_change_auto_revokes_shared_assignment_reader(tmp_path):
    accounts, esp, assignment_store = _stores(tmp_path)
    agent = _active(accounts, esp, "agent-auto-revoke@example.com", "agent")
    creator = _active(accounts, esp, "creator-auto-revoke@example.com", "creator")
    assignment_store.assign(agent["id"], creator["id"], actor="Owner")

    assert [row["creator_user_id"] for row in assignment_store.for_agent(agent["id"])] == [creator["id"]]

    esp.set_role(creator["id"], "agent", "Owner")

    assert assignment_store.for_agent(agent["id"]) == []
    row = _assignment_row(assignment_store, agent["id"], creator["id"])
    assert row["status"] == "revoked"
    assert row["revoked_by"] == "system:esp-role-boundary"
    assert row["revoked_at"]


def test_agent_role_change_auto_revokes_outgoing_assignments(tmp_path):
    accounts, esp, assignment_store = _stores(tmp_path)
    agent = _active(accounts, esp, "agent-outgoing-revoke@example.com", "agent")
    creator = _active(accounts, esp, "creator-stays-active@example.com", "creator")
    assignment_store.assign(agent["id"], creator["id"], actor="Owner")

    esp.set_role(agent["id"], "creator", "Owner")

    assert assignment_store.for_agent(agent["id"]) == []
    row = _assignment_row(assignment_store, agent["id"], creator["id"])
    assert row["status"] == "revoked"
    assert row["revoked_by"] == "system:esp-role-boundary"
    assert row["revoked_at"]


def test_membership_revoke_auto_retires_assignment_audit_record(tmp_path):
    accounts, esp, assignment_store = _stores(tmp_path)
    agent = _active(accounts, esp, "agent-status-revoke@example.com", "agent")
    creator = _active(accounts, esp, "creator-status-revoke@example.com", "creator")
    assignment_store.assign(agent["id"], creator["id"], actor="Owner")

    esp.revoke(creator["id"], "Owner")

    assert assignment_store.for_agent(agent["id"]) == []
    row = _assignment_row(assignment_store, agent["id"], creator["id"])
    assert row["status"] == "revoked"
    assert row["revoked_by"] == "system:esp-role-boundary"
    assert row["revoked_at"]

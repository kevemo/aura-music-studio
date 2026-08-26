from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_roster import AgentRosterStore, router
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore


def _active(accounts: AccountStore, esp: EspStore, email: str, role: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], role, email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", role, "Owner")
    return user


def test_agent_roster_shows_only_explicit_assignments(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    agent = _active(accounts, esp, "agent@example.com", "agent")
    assigned = _active(accounts, esp, "assigned@example.com", "creator")
    unassigned = _active(accounts, esp, "unassigned@example.com", "creator")
    assignment_store = EspAgentAssignmentStore(esp)
    assignment_store.assign(agent["id"], assigned["id"], actor="Owner")
    roster_store = AgentRosterStore(esp, assignment_store)

    rows = roster_store.roster(agent["id"])
    assert [row["creator_user_id"] for row in rows] == [assigned["id"]]
    assert unassigned["id"] not in {row["creator_user_id"] for row in rows}


def test_agent_followups_cannot_target_unassigned_creator(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    agent = _active(accounts, esp, "agent2@example.com", "agent")
    creator = _active(accounts, esp, "creator2@example.com", "creator")
    assignment_store = EspAgentAssignmentStore(esp)
    roster_store = AgentRosterStore(esp, assignment_store)

    with pytest.raises(PermissionError, match="not actively assigned"):
        roster_store.add_followup(agent["id"], creator["id"], title="Check in")


def test_followup_is_owned_by_agent_and_can_be_completed(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    agent = _active(accounts, esp, "agent3@example.com", "agent")
    other_agent = _active(accounts, esp, "agent4@example.com", "agent")
    creator = _active(accounts, esp, "creator3@example.com", "creator")
    assignment_store = EspAgentAssignmentStore(esp)
    assignment_store.assign(agent["id"], creator["id"], actor="Owner")
    roster_store = AgentRosterStore(esp, assignment_store)
    followup = roster_store.add_followup(agent["id"], creator["id"], title="Review latest LIVE")

    with pytest.raises(KeyError):
        roster_store.complete_followup(other_agent["id"], followup["id"])
    roster_store.complete_followup(agent["id"], followup["id"])
    row = next(item for item in roster_store.followups(agent["id"]) if item["id"] == followup["id"])
    assert row["status"] == "done"
    assert row["completed_at"]


def test_agent_roster_router_exposes_private_roster_and_followups():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/agent/roster" in paths
    assert "/command-center/api/agent/roster" in paths
    assert "/command-center/api/agent/followups" in paths
    assert "/command-center/api/agent/followups/{followup_id}/complete" in paths

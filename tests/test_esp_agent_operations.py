from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_operations import AgentOperationsStore, router
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


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    EspNicheStore(esp)
    agent = _active(accounts, esp, "agent-ops@example.com", "agent")
    creator = _active(accounts, esp, "creator-ops@example.com", "creator")
    other = _active(accounts, esp, "other-ops@example.com", "creator")
    assignments = EspAgentAssignmentStore(esp)
    assignments.assign(agent["id"], creator["id"], actor="Owner")
    roster = AgentRosterStore(esp, assignments)
    return accounts, esp, agent, creator, other, roster


def test_operations_reject_unassigned_creator(tmp_path):
    _accounts, _esp, agent, _creator, other, roster = _setup(tmp_path)
    store = AgentOperationsStore(roster)
    with pytest.raises(PermissionError, match="not actively assigned"):
        store.add_checkin(agent["id"], other["id"], checkin_type="routine", summary="Check in")
    with pytest.raises(PermissionError, match="not actively assigned"):
        store.start_plan(agent["id"], other["id"], pathway="build", objective="Improve retention")


def test_checkin_lifecycle_is_agent_scoped(tmp_path):
    _accounts, _esp, agent, creator, _other, roster = _setup(tmp_path)
    store = AgentOperationsStore(roster)
    row = store.add_checkin(
        agent["id"],
        creator["id"],
        checkin_type="performance",
        summary="Reviewed latest LIVE structure",
        next_action="Repeat introduction every 20 minutes",
    )
    assert row["status"] == "open"
    completed = store.complete_checkin(agent["id"], row["id"], outcome="Creator agreed the next action")
    assert completed["status"] == "completed"
    assert completed["completed_at"]


def test_only_one_active_success_pathway_per_agent_creator(tmp_path):
    _accounts, _esp, agent, creator, _other, roster = _setup(tmp_path)
    store = AgentOperationsStore(roster)
    first = store.start_plan(agent["id"], creator["id"], pathway="activate", objective="Complete first seven-day pathway")
    with pytest.raises(FileExistsError, match="already has an active"):
        store.start_plan(agent["id"], creator["id"], pathway="optimise", objective="Improve retention")
    done = store.set_plan_status(agent["id"], first["id"], status="completed", outcome="Activation pathway completed")
    assert done["status"] == "completed"
    second = store.start_plan(agent["id"], creator["id"], pathway="optimise", objective="Improve repeat-viewer retention")
    assert second["status"] == "active"


def test_operations_dashboard_contains_assigned_creator_only(tmp_path):
    _accounts, _esp, agent, creator, other, roster = _setup(tmp_path)
    store = AgentOperationsStore(roster)
    rows = store.dashboard(agent["id"])
    ids = {row["creator_user_id"] for row in rows}
    assert creator["id"] in ids
    assert other["id"] not in ids
    assert rows[0]["health"]["automated_penalty"] is False
    assert rows[0]["support_suggestion"]


def test_operations_routes_cover_checkins_and_pathways():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/agent/operations" in paths
    assert "/command-center/api/agent/operations" in paths
    assert "/command-center/api/agent/operations/checkins" in paths
    assert "/command-center/api/agent/operations/checkins/{checkin_id}/complete" in paths
    assert "/command-center/api/agent/operations/plans" in paths
    assert "/command-center/api/agent/operations/plans/{plan_id}" in paths

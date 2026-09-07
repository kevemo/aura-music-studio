from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_roster import AgentRosterStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.esp_niche import EspNicheStore
from aura_music_studio.esp_support_center import SupportCaseStore
import aura_music_studio.esp_support_escalation as escalation_mod
from aura_music_studio.esp_support_escalation import SupportEscalationStore, router
from aura_music_studio.esp_agent_roster_overlay import router as agent_overlay_router


def _active(accounts: AccountStore, esp: EspStore, email: str, role: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], role, email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", role, "Owner")
    return user


def _setup(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    EspNicheStore(esp)
    agent = _active(accounts, esp, "agent-one@example.com", "agent")
    other_agent = _active(accounts, esp, "agent-two@example.com", "agent")
    creator = _active(accounts, esp, "creator@example.com", "creator")
    assignments = EspAgentAssignmentStore(esp)
    assignments.assign(agent["id"], creator["id"], actor="Owner")
    roster = AgentRosterStore(esp, assignments)
    support = SupportCaseStore(esp)
    monkeypatch.setattr(escalation_mod, "support", support)
    monkeypatch.setattr(escalation_mod, "rosters", roster)
    escalations = SupportEscalationStore(db_path=str(esp.db_path))
    case = support.create_case(
        creator["id"],
        category="technical",
        severity="high",
        subject="LIVE audio routing problem",
        description="Creator needs private technical help with an audio routing issue.",
    )
    return accounts, esp, assignments, roster, support, escalations, agent, other_agent, creator, case


def test_only_assigned_agent_or_owner_can_see_creator_case(tmp_path, monkeypatch):
    _accounts, _esp, _assignments, _roster, _support, desk, agent, other_agent, _creator, case = _setup(tmp_path, monkeypatch)
    rows = desk.list_for_actor(agent["id"], owner=False)
    assert [row["id"] for row in rows] == [case["id"]]
    assert desk.list_for_actor(other_agent["id"], owner=False) == []
    with pytest.raises(PermissionError, match="not actively assigned"):
        desk._project(case["id"], other_agent["id"], owner=False)
    assert [row["id"] for row in desk.list_for_actor("owner", owner=True)] == [case["id"]]


def test_claim_requires_active_agent_assignment(tmp_path, monkeypatch):
    _accounts, _esp, _assignments, _roster, _support, desk, agent, other_agent, _creator, case = _setup(tmp_path, monkeypatch)
    claimed = desk.claim(case["id"], agent["id"], owner=False)
    assert claimed["workflow"]["lead_agent_user_id"] == agent["id"]
    assert claimed["workflow"]["claimed_at"]
    assert claimed["workflow"]["target_response_at"]

    with pytest.raises(PermissionError, match="only for themselves"):
        desk.claim(case["id"], agent["id"], owner=False, requested_agent_user_id=other_agent["id"])
    with pytest.raises(ValueError, match="active assignment"):
        desk.claim(case["id"], "owner", owner=True, requested_agent_user_id=other_agent["id"])


def test_internal_notes_are_never_added_to_creator_case_projection(tmp_path, monkeypatch):
    _accounts, _esp, _assignments, _roster, support, desk, agent, _other_agent, creator, case = _setup(tmp_path, monkeypatch)
    projected = desk.add_internal_note(
        case["id"],
        agent["id"],
        owner=False,
        note="Agent-only diagnostic context for the technical desk.",
    )
    assert len(projected["internal_notes"]) == 1
    assert projected["internal_notes_visible_to_creator"] is False

    creator_view = support.get(case["id"], user_id=creator["id"])
    assert "internal_notes" not in creator_view
    assert "workflow" not in creator_view
    assert all(event["action"] != "support_internal_note_added" or "note" not in event.get("metadata", {}) for event in creator_view["activity"])


def test_escalation_is_audited_and_moves_open_case_to_human_triage(tmp_path, monkeypatch):
    _accounts, _esp, _assignments, _roster, support, desk, agent, _other_agent, creator, case = _setup(tmp_path, monkeypatch)
    escalated = desk.escalate(
        case["id"],
        agent["id"],
        owner=False,
        target="technical",
        reason="Audio-routing issue needs specialist review after the normal creator check-in.",
    )
    assert escalated["status"] == "triage"
    assert escalated["workflow"]["escalation_target"] == "technical"
    assert "specialist review" in escalated["workflow"]["escalation_reason"]
    assert escalated["workflow"]["target_response_at"]
    assert escalated["health"] is not None
    assert escalated["health"]["automated_penalty"] is False

    creator_view = support.get(case["id"], user_id=creator["id"])
    assert any(event["action"] == "support_case_escalated" for event in creator_view["activity"])
    assert creator_view["status"] == "triage"


def test_resolve_requires_human_outcome_and_only_owner_can_close(tmp_path, monkeypatch):
    _accounts, _esp, _assignments, _roster, _support, desk, agent, _other_agent, _creator, case = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="human-written resolution"):
        desk.set_status(case["id"], agent["id"], owner=False, status="resolved", resolution="")
    with pytest.raises(PermissionError, match="Only ESP ownership"):
        desk.set_status(case["id"], agent["id"], owner=False, status="closed", resolution="Issue resolved.")

    resolved = desk.set_status(
        case["id"],
        agent["id"],
        owner=False,
        status="resolved",
        resolution="Creator and technical lead confirmed the routing correction worked.",
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"]

    closed = desk.set_status(
        case["id"],
        "owner",
        owner=True,
        status="closed",
        resolution="Owner reviewed the resolution and closed the private case.",
    )
    assert closed["status"] == "closed"


def test_revoked_assignment_immediately_removes_existing_case_access(tmp_path, monkeypatch):
    _accounts, _esp, assignments, _roster, _support, desk, agent, _other_agent, creator, case = _setup(tmp_path, monkeypatch)
    assert desk._project(case["id"], agent["id"], owner=False)["id"] == case["id"]
    assignments.revoke(agent["id"], creator["id"], actor="Owner")
    assert desk.list_for_actor(agent["id"], owner=False) == []
    with pytest.raises(PermissionError, match="not actively assigned"):
        desk.add_internal_note(case["id"], agent["id"], owner=False, note="Should no longer be accepted")


def test_routes_are_private_agent_overlay_paths():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/agent/support" in paths
    assert "/command-center/api/agent/support/cases" in paths
    assert "/command-center/api/agent/support/cases/{case_id}/claim" in paths
    assert "/command-center/api/agent/support/cases/{case_id}/status" in paths
    assert "/command-center/api/agent/support/cases/{case_id}/internal-notes" in paths
    assert "/command-center/api/agent/support/cases/{case_id}/escalate" in paths

    # FastAPI nests included APIRouters and does not guarantee that child paths appear as
    # top-level entries in ``router.routes``. Prove the production overlay registration through
    # actual ASGI dispatch instead: unauthenticated requests may be denied, but must not be 404.
    app = FastAPI()
    app.include_router(agent_overlay_router)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/command-center/agent/support").status_code != 404
    assert client.get("/command-center/api/agent/support/cases").status_code != 404

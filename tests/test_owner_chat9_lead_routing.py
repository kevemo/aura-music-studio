from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.owner_chat9_lead_routing as routing
from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_product_workflows import Chat9WorkflowStore, LeadCreate, StaleVersionError


def _active_user(accounts: AccountStore, email: str, display: str) -> dict:
    signup = accounts.signup(email, display, "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")


def _esp_role(esp: EspStore, user_id: str, role: str, *, region: str = "UK+", status: str = "active") -> None:
    with esp._connect() as con:
        con.execute(
            """INSERT INTO esp_memberships(user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
               VALUES (?,?,?,?,?,datetime('now'),'test-owner',datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,roles=excluded.roles,region=excluded.region,
                 approved_at=excluded.approved_at,approved_by=excluded.approved_by,updated_at=excluded.updated_at""",
            (user_id, status, role, "", region),
        )


def _store(tmp_path):
    accounts = AccountStore(tmp_path / "chat9-owner-routing.sqlite3")
    esp = EspStore(accounts)
    return accounts, esp, Chat9WorkflowStore(esp)


def _lead(handle: str = "routing.lead") -> LeadCreate:
    return LeadCreate(platform="TikTok", handle=handle, region="UK+", niche="music", source="test")


def test_routing_candidates_are_current_agents_and_region_is_advisory(tmp_path, monkeypatch):
    accounts, esp, workflows = _store(tmp_path)
    agent_uk = _active_user(accounts, "uk@example.com", "UK Agent")
    agent_us = _active_user(accounts, "us@example.com", "US Agent")
    creator = _active_user(accounts, "creator@example.com", "Creator")
    revoked = _active_user(accounts, "revoked@example.com", "Revoked Agent")
    _esp_role(esp, agent_uk["id"], "agent", region="UK+")
    _esp_role(esp, agent_us["id"], "both", region="USA & Canada")
    _esp_role(esp, creator["id"], "creator", region="UK+")
    _esp_role(esp, revoked["id"], "agent", region="UK+", status="revoked")
    monkeypatch.setattr(routing, "workflows", workflows)

    lead = workflows.create_lead(_lead(), actor_user_id=agent_uk["id"], assigned_agent_user_id=agent_uk["id"])
    result = routing.routing_candidates(lead["id"])

    by_id = {row["user_id"]: row for row in result["eligible_assignees"]}
    assert set(by_id) == {agent_uk["id"], agent_us["id"]}
    assert by_id[agent_uk["id"]]["region_alignment"] == "match"
    assert by_id[agent_us["id"]]["region_alignment"] == "different"
    assert creator["id"] not in by_id
    assert revoked["id"] not in by_id
    assert result["routing_limits"] == {
        "automatic_assignment": False,
        "region": "advisory_only",
        "capacity": "not_modeled",
        "recruiting_niche_specialization": "not_modeled",
        "selection_authority": "authenticated_owner_only",
    }


def test_owner_transfer_is_versioned_and_records_immutable_history(tmp_path, monkeypatch):
    accounts, esp, workflows = _store(tmp_path)
    first = _active_user(accounts, "first@example.com", "First Agent")
    second = _active_user(accounts, "second@example.com", "Second Agent")
    _esp_role(esp, first["id"], "agent", region="UK+")
    _esp_role(esp, second["id"], "agent", region="USA & Canada")
    monkeypatch.setattr(routing, "workflows", workflows)

    lead = workflows.create_lead(_lead("transfer.me"), actor_user_id=first["id"], assigned_agent_user_id=first["id"])
    transferred = routing.transfer_lead(
        lead["id"],
        expected_version=lead["version"],
        assigned_agent_user_id=second["id"],
        reason="Owner workload redistribution",
        actor="Mary · ESP Owner",
    )

    updated = transferred["lead"]
    assert updated["assigned_agent_user_id"] == second["id"]
    assert updated["version"] == lead["version"] + 1
    assert transferred["routing_advisory"]["region_alignment"] == "different"
    assert transferred["routing_advisory"]["capacity"] == "not_modeled"
    event = updated["history"][-1]
    assert event["action"] == "lead_transferred"
    assert event["details"]["previous_agent_user_id"] == first["id"]
    assert event["details"]["assigned_agent_user_id"] == second["id"]
    assert event["details"]["reason"] == "Owner workload redistribution"

    with pytest.raises(StaleVersionError, match="stale_version"):
        routing.transfer_lead(
            lead["id"],
            expected_version=lead["version"],
            assigned_agent_user_id=first["id"],
            reason="stale transfer",
            actor="Kev · ESP Owner",
        )


def test_transfer_rechecks_current_agent_authority(tmp_path, monkeypatch):
    accounts, esp, workflows = _store(tmp_path)
    first = _active_user(accounts, "one@example.com", "One Agent")
    revoked = _active_user(accounts, "gone@example.com", "Gone Agent")
    _esp_role(esp, first["id"], "agent")
    _esp_role(esp, revoked["id"], "agent", status="revoked")
    monkeypatch.setattr(routing, "workflows", workflows)

    lead = workflows.create_lead(_lead("authority.check"), actor_user_id=first["id"], assigned_agent_user_id=first["id"])
    with pytest.raises(ValueError, match="active ESP Agent/Both/Owner authority"):
        routing.transfer_lead(
            lead["id"],
            expected_version=lead["version"],
            assigned_agent_user_id=revoked["id"],
            reason="must fail",
            actor="ESP Owner",
        )


def test_owner_routes_fail_closed_and_allow_authenticated_manual_transfer(tmp_path, monkeypatch):
    accounts, esp, workflows = _store(tmp_path)
    first = _active_user(accounts, "route1@example.com", "Route One")
    second = _active_user(accounts, "route2@example.com", "Route Two")
    _esp_role(esp, first["id"], "agent", region="UK+")
    _esp_role(esp, second["id"], "agent", region="UK+")
    monkeypatch.setattr(routing, "workflows", workflows)
    lead = workflows.create_lead(_lead("owner.route"), actor_user_id=first["id"], assigned_agent_user_id=first["id"])

    app = FastAPI()
    app.include_router(routing.router)
    client = TestClient(app)

    monkeypatch.setattr(routing, "owner_session_authorized", lambda request: False)
    assert client.get(f"/owner/api/chat9-lead-routing/{lead['id']}").status_code == 403
    assert client.post(
        f"/owner/api/chat9-lead-routing/{lead['id']}",
        json={"expected_version": lead["version"], "assigned_agent_user_id": second["id"], "reason": "Owner route test"},
    ).status_code == 403

    monkeypatch.setattr(routing, "owner_session_authorized", lambda request: True)
    candidates = client.get(f"/owner/api/chat9-lead-routing/{lead['id']}")
    assert candidates.status_code == 200
    response = client.post(
        f"/owner/api/chat9-lead-routing/{lead['id']}",
        json={"expected_version": lead["version"], "assigned_agent_user_id": second["id"], "reason": "Owner route test"},
    )
    assert response.status_code == 200
    assert response.json()["lead"]["assigned_agent_user_id"] == second["id"]

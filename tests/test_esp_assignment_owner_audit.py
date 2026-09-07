from __future__ import annotations

import aura_music_studio.esp_owner_access_portal as portal
from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.owner_user_control import OwnerUserControl


def _active(accounts: AccountStore, esp: EspStore, email: str, role: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], role, email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", role, "Owner")
    return user


def _owner_assignment_context(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    control = OwnerUserControl(accounts, esp)
    assignments = EspAgentAssignmentStore(esp)
    agent = _active(accounts, esp, "audit-agent@example.com", "agent")
    creator = _active(accounts, esp, "audit-creator@example.com", "creator")
    monkeypatch.setattr(portal, "control", control)
    monkeypatch.setattr(portal, "assignments", assignments)
    monkeypatch.setattr(portal, "_authorized", lambda _request: True)
    return control, assignments, agent, creator


def test_owner_assignment_writes_central_audit_without_note_text(tmp_path, monkeypatch):
    control, assignments, agent, creator = _owner_assignment_context(tmp_path, monkeypatch)
    private_note = "Private mentoring context that must not be duplicated into owner audit"

    response = portal.assign_creator(
        object(),
        agent_user_id=agent["id"],
        creator_user_id=creator["id"],
        note=private_note,
    )

    assert response.status_code == 303
    audit = control.audit_log(creator["id"], 10)[0]
    assert audit["action"] == "esp_agent_creator_assigned"
    assert audit["actor"] == "ESP Owner"
    assert audit["target_user_id"] == creator["id"]
    assert audit["before"] == {}
    assert audit["after"]["agent_user_id"] == agent["id"]
    assert audit["after"]["creator_user_id"] == creator["id"]
    assert audit["after"]["status"] == "active"
    assert audit["metadata"]["relationship"] == "agent_creator"
    assert audit["metadata"]["agent_user_id"] == agent["id"]
    assert audit["metadata"]["note_present"] is True
    assert audit["metadata"]["subscription_changed"] is False
    assert private_note not in str(audit)

    with assignments._connect() as con:
        stored = con.execute(
            "SELECT note FROM esp_agent_creator_assignments WHERE agent_user_id=? AND creator_user_id=?",
            (agent["id"], creator["id"]),
        ).fetchone()
    assert stored["note"] == private_note


def test_owner_assignment_revoke_records_before_and_after_state(tmp_path, monkeypatch):
    control, _assignments, agent, creator = _owner_assignment_context(tmp_path, monkeypatch)
    portal.assign_creator(
        object(),
        agent_user_id=agent["id"],
        creator_user_id=creator["id"],
        note="",
    )

    response = portal.revoke_assignment(
        object(),
        agent_user_id=agent["id"],
        creator_user_id=creator["id"],
    )

    assert response.status_code == 303
    audit = control.audit_log(creator["id"], 10)[0]
    assert audit["action"] == "esp_agent_creator_revoked"
    assert audit["before"]["status"] == "active"
    assert audit["after"]["status"] == "revoked"
    assert audit["after"]["revoked_by"] == "ESP Owner"
    assert audit["after"]["revoked_at"]
    assert audit["metadata"]["agent_user_id"] == agent["id"]
    assert audit["metadata"]["assignment_id"] == audit["after"]["id"]
    assert audit["metadata"]["note_present"] is False


def test_unauthorized_owner_request_cannot_mutate_or_audit_assignment(tmp_path, monkeypatch):
    control, assignments, agent, creator = _owner_assignment_context(tmp_path, monkeypatch)
    monkeypatch.setattr(portal, "_authorized", lambda _request: False)

    response = portal.assign_creator(
        object(),
        agent_user_id=agent["id"],
        creator_user_id=creator["id"],
        note="blocked",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/owner"
    assert control.audit_log(creator["id"], 10) == []
    with assignments._connect() as con:
        row = con.execute(
            "SELECT id FROM esp_agent_creator_assignments WHERE agent_user_id=? AND creator_user_id=?",
            (agent["id"], creator["id"]),
        ).fetchone()
    assert row is None

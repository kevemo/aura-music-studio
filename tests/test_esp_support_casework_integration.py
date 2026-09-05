from __future__ import annotations

from aura_music_studio.accounts import AccountStore
from aura_music_studio.audit import AuditLedger
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.esp_support_casework import (
    CaseAssignmentCreate,
    CaseEscalationEventCreate,
    CaseMessageCreate,
    SupportCaseworkStore,
)
from aura_music_studio.esp_support_casework_integration import install_support_casework_integration
from aura_music_studio.esp_support_center import SupportCaseStore
from aura_music_studio.esp_support_sla import EspSupportSlaStore


def _user(accounts: AccountStore, email: str, name: str) -> dict:
    signup = accounts.signup(email, name, "a-very-secure-test-password", "base")
    return accounts.decide_membership(signup.approval_token, "approve", "Test Owner")


def _role(esp: EspStore, user_id: str, role: str, *, owner: bool = False) -> None:
    with esp._connect() as con:
        con.execute(
            """INSERT INTO esp_memberships
               (user_id,status,roles,tiktok_handle,region,approved_at,approved_by,updated_at)
               VALUES (?,?,?,?,?,datetime('now'),'test-owner',datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,roles=excluded.roles,
                 approved_at=excluded.approved_at,approved_by=excluded.approved_by,updated_at=excluded.updated_at""",
            (user_id, "owner" if owner else "active", role, "", "UK+"),
        )


def _fixture(tmp_path):
    accounts = AccountStore(tmp_path / "support-integrated.sqlite3")
    esp = EspStore(accounts)
    support = SupportCaseStore(esp)
    sla = EspSupportSlaStore(esp, support)
    assignments = EspAgentAssignmentStore(esp)
    creator = _user(accounts, "creator@example.com", "Creator")
    agent = _user(accounts, "agent@example.com", "Agent")
    owner = _user(accounts, "owner@example.com", "Owner")
    _role(esp, creator["id"], "creator")
    _role(esp, agent["id"], "agent")
    _role(esp, owner["id"], "owner", owner=True)
    assignments.assign(agent["id"], creator["id"], actor=owner["id"])
    case = support.create_case(
        creator["id"], category="technical", severity="high", subject="Blocked LIVE", description="Needs staff response"
    )
    sla.attach_existing(case["id"], category="live_technical", priority="P1", region="UK+")
    install_support_casework_integration()
    casework = SupportCaseworkStore(esp, support, AuditLedger(accounts), storage_root=tmp_path / "support-files")
    return esp, casework, creator, agent, owner, case


def test_creator_visible_staff_reply_updates_sla_once(tmp_path):
    esp, casework, _creator, agent, _owner, case = _fixture(tmp_path)
    body = CaseMessageCreate(
        body="A human has reviewed this and here is the next action.",
        visibility="creator",
        expected_revision=0,
        idempotency_key="sla-reply-0001",
    )
    first = casework.add_message(case["id"], agent["id"], body)
    second = casework.add_message(case["id"], agent["id"], body)
    assert first == second

    with esp._connect() as con:
        meta = con.execute("SELECT * FROM esp_support_service_meta WHERE case_id=?", (case["id"],)).fetchone()
        touches = con.execute(
            "SELECT * FROM esp_support_service_touches WHERE case_id=? AND kind='casework_creator_reply'",
            (case["id"],),
        ).fetchall()
    assert meta["acknowledged_at"]
    assert meta["first_substantive_response_at"]
    assert meta["last_creator_update_at"]
    assert len(touches) == 1
    assert touches[0]["note"] == f"casework-message:{first['message_id']}"


def test_internal_note_does_not_satisfy_creator_response_sla(tmp_path):
    esp, casework, _creator, agent, _owner, case = _fixture(tmp_path)
    casework.add_message(
        case["id"],
        agent["id"],
        CaseMessageCreate(
            body="Internal diagnostic note only.",
            visibility="internal",
            expected_revision=0,
            idempotency_key="sla-internal-0001",
        ),
    )
    with esp._connect() as con:
        meta = con.execute("SELECT * FROM esp_support_service_meta WHERE case_id=?", (case["id"],)).fetchone()
        count = con.execute(
            "SELECT COUNT(*) FROM esp_support_service_touches WHERE case_id=? AND kind='casework_creator_reply'",
            (case["id"],),
        ).fetchone()[0]
    assert meta["first_substantive_response_at"] is None
    assert count == 0


def test_casework_assignment_and_escalation_sync_current_agent_workflow_state(tmp_path):
    esp, casework, _creator, agent, owner, case = _fixture(tmp_path)
    casework.assign(
        case["id"],
        owner["id"],
        CaseAssignmentCreate(
            assignee_user_id=agent["id"],
            reason="Primary support owner",
            expected_revision=0,
            idempotency_key="sync-assignment-0001",
        ),
    )
    casework.record_escalation(
        case["id"],
        agent["id"],
        CaseEscalationEventCreate(
            target="technical",
            reason="Specialist audio review required",
            expected_revision=1,
            idempotency_key="sync-escalation-0001",
        ),
    )
    with esp._connect() as con:
        workflow = con.execute("SELECT * FROM esp_support_case_workflow WHERE case_id=?", (case["id"],)).fetchone()
    assert workflow["lead_agent_user_id"] == agent["id"]
    assert workflow["escalation_target"] == "technical"
    assert workflow["escalation_reason"] == "Specialist audio review required"
    assert workflow["claimed_at"]
    assert workflow["escalated_at"]

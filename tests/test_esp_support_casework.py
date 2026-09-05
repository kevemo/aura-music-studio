from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.audit import AuditLedger
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.esp_support_casework import (
    CaseAssignmentCreate,
    CaseEscalationEventCreate,
    CaseMessageCreate,
    CaseRelationCreate,
    SupportCaseworkStore,
    router as casework_router,
)
from aura_music_studio.esp_support_center import SupportCaseStore


def _active_user(accounts: AccountStore, email: str, display: str) -> dict:
    signup = accounts.signup(email, display, "a-very-secure-test-password", "base")
    return accounts.decide_membership(signup.approval_token, "approve", "Test Owner")


def _role(esp: EspStore, user_id: str, role: str, *, owner: bool = False) -> None:
    status = "owner" if owner else "active"
    with esp._connect() as con:
        con.execute(
            """INSERT INTO esp_memberships
               (user_id,status,roles,tiktok_handle,region,requested_at,approved_at,approved_by,revoked_at,revoked_by,updated_at)
               VALUES (?,?,?,?,?,NULL,datetime('now'),'test-owner',NULL,NULL,datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,roles=excluded.roles,
                 approved_at=excluded.approved_at,approved_by=excluded.approved_by,revoked_at=NULL,
                 revoked_by=NULL,updated_at=excluded.updated_at""",
            (user_id, status, role, "", "UK+"),
        )


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "support-casework.sqlite3")
    esp = EspStore(accounts)
    support = SupportCaseStore(esp)
    assignments = EspAgentAssignmentStore(esp)
    casework = SupportCaseworkStore(
        esp,
        support,
        AuditLedger(accounts),
        storage_root=tmp_path / "private-support",
    )
    return accounts, esp, support, assignments, casework


def _fixture(tmp_path):
    accounts, esp, support, assignments, casework = _stores(tmp_path)
    creator = _active_user(accounts, "creator@example.com", "Creator")
    other_creator = _active_user(accounts, "other@example.com", "Other Creator")
    agent = _active_user(accounts, "agent@example.com", "Agent")
    owner = _active_user(accounts, "owner@example.com", "Owner")
    _role(esp, creator["id"], "creator")
    _role(esp, other_creator["id"], "creator")
    _role(esp, agent["id"], "agent")
    _role(esp, owner["id"], "owner", owner=True)
    assignments.assign(agent["id"], creator["id"], actor=owner["id"], note="Support mentor")
    case = support.create_case(
        creator["id"],
        category="technical",
        severity="normal",
        subject="LIVE audio routing",
        description="Creator needs private technical support.",
    )
    return accounts, esp, support, assignments, casework, creator, other_creator, agent, owner, case


def test_creator_visible_reply_and_internal_note_are_strictly_separated(tmp_path):
    _accounts, _esp, _support, _assignments, casework, creator, _other, agent, _owner, case = _fixture(tmp_path)
    assert casework.revision(case["id"]) == 0

    visible = casework.add_message(
        case["id"],
        agent["id"],
        CaseMessageCreate(
            body="Please try the documented audio-routing reset and tell us the result.",
            visibility="creator",
            expected_revision=0,
            idempotency_key="visible-reply-0001",
        ),
    )
    internal = casework.add_message(
        case["id"],
        agent["id"],
        CaseMessageCreate(
            body="Internal: verify mixer profile before escalating.",
            visibility="internal",
            expected_revision=1,
            idempotency_key="internal-note-0001",
        ),
    )
    assert visible["revision"] == 1
    assert internal["revision"] == 2

    creator_view = casework.project(case["id"], creator["id"])
    assert [row["body"] for row in creator_view["messages"]] == [
        "Please try the documented audio-routing reset and tell us the result."
    ]
    assert creator_view["view"] == "creator"

    staff_view = casework.project(case["id"], agent["id"])
    assert len(staff_view["messages"]) == 2
    assert {row["visibility"] for row in staff_view["messages"]} == {"creator", "internal"}

    with pytest.raises(PermissionError):
        casework.add_message(
            case["id"],
            creator["id"],
            CaseMessageCreate(
                body="Trying to create an internal note",
                visibility="internal",
                expected_revision=2,
                idempotency_key="creator-internal-0001",
            ),
        )


def test_idempotency_returns_same_result_and_stale_revision_fails_closed(tmp_path):
    _accounts, _esp, _support, _assignments, casework, creator, _other, _agent, _owner, case = _fixture(tmp_path)
    body = CaseMessageCreate(
        body="Creator follow-up",
        visibility="creator",
        expected_revision=0,
        idempotency_key="creator-followup-0001",
    )
    first = casework.add_message(case["id"], creator["id"], body)
    repeated = casework.add_message(case["id"], creator["id"], body)
    assert repeated == first
    assert casework.revision(case["id"]) == 1
    assert len(casework.project(case["id"], creator["id"])["messages"]) == 1

    with pytest.raises(RuntimeError, match="revision_conflict:1"):
        casework.add_message(
            case["id"],
            creator["id"],
            CaseMessageCreate(
                body="Stale-tab write",
                visibility="creator",
                expected_revision=0,
                idempotency_key="stale-write-0001",
            ),
        )


def test_private_attachments_have_acl_deduplication_and_never_expose_storage_path(tmp_path):
    _accounts, _esp, _support, _assignments, casework, creator, _other, agent, _owner, case = _fixture(tmp_path)
    internal = casework.add_attachment(
        case["id"],
        agent["id"],
        filename="diagnostic screenshot.png",
        content_type="image/png",
        content=b"not-a-real-image-but-private-test-bytes",
        visibility="internal",
        expected_revision=0,
        idempotency_key="internal-file-0001",
    )
    assert internal["revision"] == 1
    staff_view = casework.project(case["id"], agent["id"])
    attachment = staff_view["attachments"][0]
    assert attachment["private_storage_path_exposed"] is False
    assert "storage_path" not in attachment
    assert attachment["download_path"].endswith(f"/{internal['attachment_id']}/download")

    path, metadata = casework.attachment_file(case["id"], internal["attachment_id"], agent["id"])
    assert path.is_file()
    assert metadata["original_name"] == "diagnostic_screenshot.png"
    with pytest.raises(PermissionError):
        casework.attachment_file(case["id"], internal["attachment_id"], creator["id"])
    assert casework.project(case["id"], creator["id"])["attachments"] == []

    with pytest.raises(FileExistsError, match="duplicate_attachment"):
        casework.add_attachment(
            case["id"],
            agent["id"],
            filename="renamed.png",
            content_type="image/png",
            content=b"not-a-real-image-but-private-test-bytes",
            visibility="internal",
            expected_revision=1,
            idempotency_key="internal-file-0002",
        )
    assert casework.revision(case["id"]) == 1


def test_creator_attachment_is_visible_only_inside_own_case_scope(tmp_path):
    _accounts, _esp, support, _assignments, casework, creator, other, _agent, _owner, case = _fixture(tmp_path)
    uploaded = casework.add_attachment(
        case["id"],
        creator["id"],
        filename="evidence.pdf",
        content_type="application/pdf",
        content=b"%PDF-1.4 private evidence",
        visibility="creator",
        expected_revision=0,
        idempotency_key="creator-file-0001",
    )
    creator_view = casework.project(case["id"], creator["id"])
    assert creator_view["attachments"][0]["id"] == uploaded["attachment_id"]

    with pytest.raises(PermissionError):
        casework.project(case["id"], other["id"])
    with pytest.raises(PermissionError):
        casework.attachment_file(case["id"], uploaded["attachment_id"], other["id"])

    other_case = support.create_case(
        other["id"], category="technical", severity="normal", subject="Other case", description="Other private case"
    )
    assert casework.project(other_case["id"], other["id"])["attachments"] == []


def test_duplicate_related_ticket_and_related_record_links_are_staff_managed(tmp_path):
    _accounts, _esp, support, _assignments, casework, creator, other, agent, owner, case = _fixture(tmp_path)
    second = support.create_case(
        creator["id"], category="technical", severity="normal", subject="Same routing issue", description="Duplicate report"
    )
    relation = casework.add_relation(
        case["id"],
        agent["id"],
        CaseRelationCreate(
            relation_type="duplicate_of",
            related_case_id=second["id"],
            label="Same underlying audio routing incident",
            visibility="internal",
            expected_revision=0,
            idempotency_key="duplicate-link-0001",
        ),
    )
    assert relation["relation_type"] == "duplicate_of"
    assert casework.project(case["id"], creator["id"])["relations"] == []
    assert casework.project(case["id"], agent["id"])["relations"][0]["related_case_id"] == second["id"]

    related_record = casework.add_relation(
        case["id"],
        owner["id"],
        CaseRelationCreate(
            relation_type="related_record",
            resource_type="creator_review",
            resource_id="review-90-day-001",
            label="90-day creator review",
            visibility="internal",
            expected_revision=1,
            idempotency_key="record-link-0001",
        ),
    )
    assert related_record["revision"] == 2

    other_case = support.create_case(
        other["id"], category="policy", severity="normal", subject="Other member", description="Separate private record"
    )
    with pytest.raises(PermissionError):
        casework.add_relation(
            case["id"],
            agent["id"],
            CaseRelationCreate(
                relation_type="related",
                related_case_id=other_case["id"],
                expected_revision=2,
                idempotency_key="cross-creator-link-0001",
            ),
        )


def test_assignment_and_escalation_history_are_durable_and_internal(tmp_path):
    _accounts, _esp, _support, _assignments, casework, creator, _other, agent, owner, case = _fixture(tmp_path)
    assigned = casework.assign(
        case["id"],
        owner["id"],
        CaseAssignmentCreate(
            assignee_user_id=agent["id"],
            reason="Primary mentor owns first response",
            expected_revision=0,
            idempotency_key="assignment-0001",
        ),
    )
    escalated = casework.record_escalation(
        case["id"],
        agent["id"],
        CaseEscalationEventCreate(
            target="technical",
            reason="Needs specialist audio-routing review",
            expected_revision=1,
            idempotency_key="escalation-0001",
        ),
    )
    assert assigned["revision"] == 1
    assert escalated["revision"] == 2
    staff_events = casework.project(case["id"], agent["id"])["events"]
    assert [event["event_type"] for event in staff_events] == ["assigned", "escalated"]
    assert staff_events[1]["details"]["target"] == "technical"
    assert casework.project(case["id"], creator["id"])["events"] == []

    with sqlite3.connect(casework.db_path) as con:
        row = con.execute("SELECT assigned_owner FROM esp_support_cases WHERE id=?", (case["id"],)).fetchone()
    assert row[0] == agent["id"]


def test_casework_routes_cover_conversation_files_relations_assignment_and_escalation():
    routes = {(getattr(route, "path", None), frozenset(getattr(route, "methods", set()) or set())) for route in casework_router.routes}
    assert any(path == "/command-center/api/support/cases/{case_id}/casework" and "GET" in methods for path, methods in routes)
    assert any(path == "/command-center/api/support/cases/{case_id}/messages" and "POST" in methods for path, methods in routes)
    assert any(path == "/command-center/api/support/cases/{case_id}/attachments" and "POST" in methods for path, methods in routes)
    assert any(path.endswith("/attachments/{attachment_id}/download") and "GET" in methods for path, methods in routes)
    assert any(path == "/command-center/api/support/cases/{case_id}/relations" and "POST" in methods for path, methods in routes)
    assert any(path == "/command-center/api/support/cases/{case_id}/assignment" and "POST" in methods for path, methods in routes)
    assert any(path == "/command-center/api/support/cases/{case_id}/escalation-history" and "POST" in methods for path, methods in routes)

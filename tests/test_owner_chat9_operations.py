from __future__ import annotations

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.owner_chat9_operations as owner_ops_module
from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_product_workflows import Chat9WorkflowStore
from aura_music_studio.esp_support_center import SupportCaseStore
from aura_music_studio.esp_training_academy import TrainingAcademyStore
from aura_music_studio.owner_chat9_operations import OwnerChat9Operations


def _active_user(accounts: AccountStore) -> dict:
    signup = accounts.signup(
        "owner-summary@example.com",
        "Owner Summary User",
        "a-very-secure-test-password",
        "free",
    )
    return accounts.decide_membership(signup.approval_token, "approve", "ESP Test Owner")


def _seed_chat9_state(tmp_path):
    accounts = AccountStore(tmp_path / "owner-chat9.sqlite3")
    user = _active_user(accounts)
    esp = EspStore(accounts)
    Chat9WorkflowStore(esp)
    SupportCaseStore(esp)
    TrainingAcademyStore(esp)
    now = "2026-09-05T20:00:00+00:00"
    future = "2099-01-01T00:00:00+00:00"

    with sqlite3.connect(accounts.db_path) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(
            """INSERT INTO esp_support_cases
               (id,user_id,category,severity,status,subject,description,created_at,updated_at)
               VALUES ('case-urgent',?,'technical','urgent','open','Need help','Details',?,?)""",
            (user["id"], now, now),
        )
        con.execute(
            """INSERT INTO esp_support_cases
               (id,user_id,category,severity,status,subject,description,created_at,updated_at)
               VALUES ('case-resolved',?,'other','normal','resolved','Done','Resolved',?,?)""",
            (user["id"], now, now),
        )

        for lead_id, status, dnc in (
            ("lead-active", "interested", 0),
            ("lead-follow", "follow_up", 0),
            ("lead-activated", "activated", 0),
            ("lead-dnc", "do_not_contact", 1),
        ):
            con.execute(
                """INSERT INTO esp_recruitment_leads
                   (id,dedupe_key,platform,handle,status,do_not_contact,created_at,updated_at)
                   VALUES (?,?, 'TikTok', ?, ?, ?, ?, ?)""",
                (lead_id, f"key-{lead_id}", lead_id, status, dnc, now, now),
            )

        con.execute(
            """INSERT INTO esp_training_courses_v2
               (id,slug,created_by,created_at) VALUES ('course-1','owner-summary-course','owner',?)""",
            (now,),
        )
        con.execute(
            """INSERT INTO esp_training_course_versions_v2
               (course_id,version,status,title,created_by,created_at,updated_at)
               VALUES ('course-1',1,'published','Owner Summary','owner',?,?)""",
            (now, now),
        )
        con.execute(
            """INSERT INTO esp_training_exam_attempts_v2
               (id,user_id,course_id,version,answers_json,score_percent,passed,created_at)
               VALUES ('attempt-fail',?,'course-1',1,'{}',50,0,?)""",
            (user["id"], now),
        )
        con.execute(
            """INSERT INTO esp_training_certificates_v2
               (id,user_id,course_id,version,issued_at)
               VALUES ('certificate-1',?,'course-1',1,?)""",
            (user["id"], now),
        )

        con.execute(
            """INSERT INTO esp_announcements
               (id,title,body,audience,acknowledgement_required,status,publish_at,expires_at,created_by,created_at,updated_at)
               VALUES ('announcement-live','Live','Body','everyone',1,'published',NULL,?,'owner',?,?)""",
            (future, now, now),
        )
        con.execute(
            """INSERT INTO esp_announcements
               (id,title,body,audience,status,publish_at,created_by,created_at,updated_at)
               VALUES ('announcement-scheduled','Later','Body','everyone','scheduled',?,'owner',?,?)""",
            (future, now, now),
        )
        con.execute(
            """INSERT INTO esp_announcement_acknowledgements
               (announcement_id,user_id,acknowledged_at)
               VALUES ('announcement-live',?,?)""",
            (user["id"], now),
        )

        con.execute(
            """INSERT INTO esp_creator_evidence_batches
               (id,creator_user_id,source_type,imported_at,uploader_user_id,raw_evidence_ref,status)
               VALUES ('batch-1',?,'manual',?,?,'evidence:owner-summary','draft')""",
            (user["id"], now, user["id"]),
        )
        con.execute(
            """INSERT INTO esp_creator_evidence_metrics
               (id,batch_id,metric_name,value_json,needs_review,created_at,updated_at)
               VALUES ('metric-1','batch-1','views','100',1,?,?)""",
            (now, now),
        )

    return accounts


def test_owner_chat9_summary_aggregates_canonical_tables(tmp_path):
    accounts = _seed_chat9_state(tmp_path)
    summary = OwnerChat9Operations(accounts).summary()

    assert summary["read_only"] is True
    assert summary["support"] == {
        "open_cases": 1,
        "high_or_urgent_open": 1,
        "waiting_member": 0,
    }
    assert summary["recruitment"] == {
        "active_leads": 2,
        "follow_up": 1,
        "activated": 1,
        "do_not_contact": 1,
    }
    assert summary["training"] == {
        "published_courses": 1,
        "certificates": 1,
        "failed_attempts": 1,
    }
    assert summary["announcements"] == {
        "active_published": 1,
        "scheduled": 1,
        "ack_required_active": 1,
        "acknowledgements_recorded": 1,
    }
    assert summary["evidence"] == {
        "draft_batches": 1,
        "metrics_needing_review": 1,
    }


def test_owner_chat9_summary_fails_soft_when_optional_chat9_tables_are_absent(tmp_path):
    accounts = AccountStore(tmp_path / "owner-chat9-empty.sqlite3")
    summary = OwnerChat9Operations(accounts).summary()

    assert summary["support"]["open_cases"] == 0
    assert summary["recruitment"]["active_leads"] == 0
    assert summary["training"]["certificates"] == 0
    assert summary["announcements"]["active_published"] == 0
    assert summary["evidence"]["metrics_needing_review"] == 0


def test_owner_chat9_operations_api_is_owner_only(tmp_path, monkeypatch):
    accounts = _seed_chat9_state(tmp_path)
    service = OwnerChat9Operations(accounts)
    monkeypatch.setattr(owner_ops_module, "operations", service)

    app = FastAPI()
    app.include_router(owner_ops_module.router)
    client = TestClient(app)

    monkeypatch.setattr(owner_ops_module, "owner_session_authorized", lambda request: False)
    denied = client.get("/owner/api/chat9-operations")
    assert denied.status_code == 403

    monkeypatch.setattr(owner_ops_module, "owner_session_authorized", lambda request: True)
    allowed = client.get("/owner/api/chat9-operations")
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["read_only"] is True
    assert payload["support"]["high_or_urgent_open"] == 1
    assert payload["recruitment"]["do_not_contact"] == 1

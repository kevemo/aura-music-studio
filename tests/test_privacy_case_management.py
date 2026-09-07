from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.privacy_case_management import PrivacyCaseStore, router
from aura_music_studio.privacy_rights import PrivacyEvidenceStore


def _privacy_request(db, *, user_id="member-a", request_type="deletion"):
    return PrivacyEvidenceStore(db).submit_request(
        user_id=user_id,
        request_type=request_type,
        detail="Please review this request.",
        locale="en-GB",
    )


def _prepare_for_fulfilment(store: PrivacyCaseStore, request_id: str) -> None:
    store.set_context(
        request_id,
        jurisdiction="United Kingdom",
        legal_basis="Owner-reviewed applicable privacy request basis",
        due_at="2026-09-26T17:00:00+00:00",
    )
    store.set_identity(
        request_id,
        status="verified",
        method="authenticated-account plus reviewed evidence",
        evidence_reference="identity-evidence:opaque-123",
    )
    store.transition(request_id, status="ready_for_fulfilment")


def test_case_requires_identity_context_holds_and_execution_evidence_before_deletion_fulfilment(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    request_row = _privacy_request(db)
    store = PrivacyCaseStore(db)

    with pytest.raises(ValueError, match="Verified identity"):
        store.transition(request_row["id"], status="ready_for_fulfilment")

    store.set_context(
        request_row["id"],
        jurisdiction="United Kingdom",
        legal_basis="Owner-reviewed applicable privacy request basis",
        due_at="2026-09-26T17:00:00+00:00",
    )
    store.set_identity(
        request_row["id"],
        status="verified",
        method="authenticated-account plus reviewed evidence",
        evidence_reference="identity-evidence:opaque-123",
    )
    store.set_holds(
        request_row["id"], legal_hold=True, retention_hold=False, reason="Active preservation requirement under review"
    )
    with pytest.raises(ValueError, match="hold blocks fulfilment"):
        store.transition(request_row["id"], status="ready_for_fulfilment")

    store.set_holds(request_row["id"], legal_hold=False, retention_hold=False)
    ready = store.transition(request_row["id"], status="ready_for_fulfilment")
    assert ready["request"]["status"] == "ready_for_fulfilment"
    assert ready["automatic_data_action_taken"] is False
    assert ready["grants_esp_role_or_permission"] is False

    with pytest.raises(ValueError, match="evidence reference"):
        store.transition(request_row["id"], status="fulfilled")

    with pytest.raises(ValueError, match="execution evidence"):
        store.transition(
            request_row["id"], status="fulfilled", fulfilment_reference="fulfilment:reviewed-output-456"
        )

    execution = store.record_deletion_execution(
        request_row["id"],
        execution_kind="anonymised",
        evidence_reference="deletion-executor:job-789",
        note="Completion evidence supplied by the responsible data subsystem.",
    )
    assert execution["control"]["deletion_execution_kind"] == "anonymised"
    assert execution["control"]["deletion_execution_reference"] == "deletion-executor:job-789"
    assert execution["control"]["deletion_executed_at"]
    execution_events = [event for event in execution["events"] if event["action"] == "deletion_execution_recorded"]
    assert execution_events[-1]["data"]["operation_performed_by_case_management"] is False

    finished = store.transition(
        request_row["id"], status="fulfilled", fulfilment_reference="fulfilment:reviewed-output-456"
    )
    assert finished["request"]["status"] == "fulfilled"
    assert finished["control"]["fulfilment_reference"] == "fulfilment:reviewed-output-456"
    assert finished["automatic_data_action_taken"] is False


def test_deletion_execution_requires_ready_deletion_case_and_retained_exception_reference_for_mixed_result(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    deletion = _privacy_request(db)
    access = _privacy_request(db, user_id="member-b", request_type="access")
    store = PrivacyCaseStore(db)

    with pytest.raises(ValueError, match="ready for fulfilment"):
        store.record_deletion_execution(
            deletion["id"], execution_kind="deleted", evidence_reference="executor:too-early"
        )

    _prepare_for_fulfilment(store, deletion["id"])
    with pytest.raises(ValueError, match="retained-exception reference"):
        store.record_deletion_execution(
            deletion["id"], execution_kind="mixed", evidence_reference="executor:mixed-1"
        )

    recorded = store.record_deletion_execution(
        deletion["id"],
        execution_kind="mixed",
        evidence_reference="executor:mixed-2",
        retained_exception_reference="retention-exception:case-4",
    )
    assert recorded["control"]["retained_exception_reference"] == "retention-exception:case-4"

    with pytest.raises(ValueError, match="only be recorded for deletion requests"):
        store.record_deletion_execution(
            access["id"], execution_kind="deleted", evidence_reference="executor:not-deletion"
        )


def test_existing_privacy_case_database_is_migrated_with_execution_columns(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute(
            """CREATE TABLE privacy_case_controls (
                request_id TEXT PRIMARY KEY,
                jurisdiction TEXT NOT NULL DEFAULT '',
                legal_basis TEXT NOT NULL DEFAULT '',
                due_at TEXT,
                identity_status TEXT NOT NULL DEFAULT 'unverified',
                identity_method TEXT NOT NULL DEFAULT '',
                identity_evidence_reference TEXT NOT NULL DEFAULT '',
                legal_hold INTEGER NOT NULL DEFAULT 0,
                retention_hold INTEGER NOT NULL DEFAULT 0,
                fulfilment_reference TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )"""
        )

    PrivacyCaseStore(db)
    with sqlite3.connect(db) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(privacy_case_controls)").fetchall()}
    assert {
        "deletion_execution_kind",
        "deletion_execution_reference",
        "deletion_executed_at",
        "retained_exception_reference",
    }.issubset(columns)


def test_verified_identity_requires_reference_and_never_stores_raw_document(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    request_row = _privacy_request(db, request_type="access")
    store = PrivacyCaseStore(db)

    with pytest.raises(ValueError, match="evidence reference"):
        store.set_identity(request_row["id"], status="verified", method="passport reviewed")

    case = store.set_identity(
        request_row["id"],
        status="verified",
        method="document reviewed out-of-band",
        evidence_reference="verify:opaque-abc",
    )
    identity_events = [event for event in case["events"] if event["action"] == "identity_reviewed"]
    assert identity_events[-1]["data"]["raw_identity_document_stored"] is False
    assert identity_events[-1]["data"]["evidence_reference"] == "verify:opaque-abc"


def test_privacy_case_events_preserve_owner_session_actor_and_record_reviewer(monkeypatch, tmp_path):
    db = tmp_path / "privacy.sqlite3"
    request_row = _privacy_request(db, request_type="access")
    monkeypatch.setattr("aura_music_studio.privacy_case_management.owner_actor", lambda: "Kev · ESP Owner")
    store = PrivacyCaseStore(db)

    case = store.set_context(
        request_row["id"],
        jurisdiction="United Kingdom",
        legal_basis="Owner-reviewed applicable privacy request basis",
        due_at=None,
        note="Attribution test",
    )
    event = [item for item in case["events"] if item["action"] == "context_reviewed"][-1]
    assert event["actor"] == "owner_session"
    assert event["data"]["reviewer_actor"] == "Kev · ESP Owner"
    assert case["audit_chain_valid"] is True

    case = store.set_identity(request_row["id"], status="pending", method="account review")
    event = [item for item in case["events"] if item["action"] == "identity_reviewed"][-1]
    assert event["actor"] == "owner_session"
    assert event["data"]["reviewer_actor"] == "Kev · ESP Owner"
    assert case["audit_chain_valid"] is True


def test_audit_chain_detects_tampering(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    request_row = _privacy_request(db)
    store = PrivacyCaseStore(db)
    store.set_context(
        request_row["id"], jurisdiction="UK", legal_basis="Reviewed basis", due_at=None, note="Original"
    )
    assert store.get_case(request_row["id"])["audit_chain_valid"] is True

    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE privacy_case_events SET data_json=? WHERE request_id=?",
            ('{"note":"tampered"}', request_row["id"]),
        )
    assert store.get_case(request_row["id"])["audit_chain_valid"] is False


def test_terminal_case_cannot_be_reopened(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    request_row = _privacy_request(db, request_type="correction")
    store = PrivacyCaseStore(db)
    denied = store.transition(request_row["id"], status="denied", reason="Owner-reviewed exception")
    assert denied["request"]["status"] == "denied"
    with pytest.raises(ValueError, match="cannot be reopened"):
        store.transition(request_row["id"], status="under_review")


def test_owner_routes_fail_closed_without_owner_session(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "privacy.sqlite3"))
    request_row = _privacy_request(tmp_path / "privacy.sqlite3")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/owner/privacy/cases").status_code == 403
    assert client.post(
        f"/owner/privacy/cases/{request_row['id']}/deletion-execution",
        json={"execution_kind": "deleted", "evidence_reference": "executor:forbidden"},
    ).status_code == 403


def test_owner_routes_use_existing_owner_authorization_and_do_not_execute_data_actions(monkeypatch, tmp_path):
    db = tmp_path / "privacy.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db))
    request_row = _privacy_request(db)
    monkeypatch.setattr("aura_music_studio.privacy_case_management.owner_authorized", lambda request: True)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    listing = client.get("/owner/privacy/cases")
    assert listing.status_code == 200
    assert listing.json()["automatic_data_action_taken"] is False
    assert listing.json()["grants_esp_role_or_permission"] is False

    context = client.post(
        f"/owner/privacy/cases/{request_row['id']}/context",
        json={
            "jurisdiction": "United Kingdom",
            "legal_basis": "Reviewed applicable privacy law basis",
            "due_at": "2026-09-26T17:00:00+00:00",
            "note": "Deadline entered after jurisdiction review",
        },
    )
    assert context.status_code == 200
    assert context.json()["automatic_data_action_taken"] is False


def test_integration_overlay_mounts_hidden_owner_privacy_routes_via_dispatch():
    from aura_music_studio.creative_version_autopromotion import router as integration_router

    app = FastAPI()
    app.include_router(integration_router)
    client = TestClient(app)

    # Owner privacy endpoints are intentionally excluded from the public OpenAPI document.
    # Prove they are mounted through actual ASGI dispatch: the owner gate must answer 403.
    assert "/owner/privacy/cases" not in app.openapi()["paths"]
    assert client.get("/owner/privacy/cases").status_code == 403
    assert client.get("/owner/privacy/cases/nonexistent").status_code == 403
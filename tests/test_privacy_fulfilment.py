from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.privacy_case_management import PrivacyCaseStore
from aura_music_studio.privacy_fulfilment import PACKAGE_VERSION, PrivacyFulfilmentStore, owner_router
from aura_music_studio.privacy_rights import PrivacyEvidenceStore, router as member_privacy_router


def _account_and_request(db, *, request_type: str = "access"):
    account = AccountStore(db).signup(
        "member@example.com",
        "Member Example",
        "strong-password-123",
        "free",
    )
    evidence = PrivacyEvidenceStore(db)
    request_row = evidence.submit_request(
        user_id=account.user_id,
        request_type=request_type,
        detail="Please provide the personal data covered by this request.",
        locale="en-GB",
    )
    evidence.record_policy_decision(
        user_id=account.user_id,
        policy_key="global_compliance_notice",
        decision="acknowledged",
        locale="en-GB",
    )
    return account, request_row


def _ready_case(db, request_id: str) -> PrivacyCaseStore:
    case = PrivacyCaseStore(db)
    case.set_context(
        request_id,
        jurisdiction="United Kingdom",
        legal_basis="Owner-reviewed applicable privacy request basis",
        due_at="2026-09-26T17:00:00+00:00",
    )
    case.set_identity(
        request_id,
        status="verified",
        method="authenticated-account plus reviewed evidence",
        evidence_reference="identity-evidence:opaque-123",
    )
    case.transition(request_id, status="ready_for_fulfilment")
    return case


def _fulfilled_disclosure(db, *, request_type: str = "access"):
    account, request_row = _account_and_request(db, request_type=request_type)
    case = _ready_case(db, request_row["id"])
    fulfilment = PrivacyFulfilmentStore(db)
    prepared = fulfilment.prepare(request_row["id"], prepared_by="Mary Ortiz")
    case.transition(
        request_row["id"],
        status="fulfilled",
        fulfilment_reference=prepared["fulfilment_reference"],
        reason="Reviewed structured disclosure is ready for authenticated member delivery.",
    )
    return account, request_row, prepared, fulfilment


def test_access_fulfilment_is_allowlisted_machine_readable_and_excludes_auth_secrets(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    account, request_row, prepared, fulfilment = _fulfilled_disclosure(db)

    result = fulfilment.deliver(request_row["id"], user_id=account.user_id)
    package = result["package"]
    assert package["schema"] == PACKAGE_VERSION
    assert package["subject"]["user_id"] == account.user_id
    assert package["request"]["request_type"] == "access"
    assert package["scope"]["structured_machine_readable_json"] is True
    assert package["scope"]["raw_database_export"] is False
    assert result["evidence"]["fulfilment_reference"] == prepared["fulfilment_reference"]
    assert result["evidence"]["package_contents_persisted"] is False

    account_section = package["sections"]["account"]
    assert account_section["email"] == "member@example.com"
    assert account_section["display_name"] == "Member Example"
    assert "password_hash" not in account_section
    assert "password_salt" not in account_section
    assert "approved_by" not in account_section
    assert "rejected_by" not in account_section

    stored_user = AccountStore(db).get_user(account.user_id)
    assert stored_user is not None
    serialized = json.dumps(package, sort_keys=True)
    assert stored_user["password_hash"] not in serialized
    assert "token_hash" not in serialized
    assert "sessions" not in package["sections"]
    assert "password_hashes_and_password_salts" in package["scope"]["excluded_categories"]
    assert result["grants_esp_role_or_permission"] is False
    assert result["changes_billing_or_membership"] is False


def test_delivery_is_member_scoped_requires_exact_fulfilled_reference_and_is_stable_after_first_delivery(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    account, request_row, prepared, fulfilment = _fulfilled_disclosure(db, request_type="portability")

    with pytest.raises(KeyError):
        fulfilment.deliver(request_row["id"], user_id="different-member")

    first = fulfilment.deliver(request_row["id"], user_id=account.user_id)
    second = fulfilment.deliver(request_row["id"], user_id=account.user_id)
    assert second["evidence"]["sha256"] == first["evidence"]["sha256"]
    assert second["evidence"]["delivered_at"] == first["evidence"]["delivered_at"]

    with sqlite3.connect(db) as con:
        con.execute("UPDATE users SET display_name='Changed After Delivery' WHERE id=?", (account.user_id,))
    with pytest.raises(RuntimeError, match="snapshot changed"):
        fulfilment.deliver(request_row["id"], user_id=account.user_id)

    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE privacy_case_controls SET fulfilment_reference='privacy-package:wrong' WHERE request_id=?",
            (request_row["id"],),
        )
    with pytest.raises(ValueError, match="does not match"):
        fulfilment.deliver(request_row["id"], user_id=account.user_id)

    assert prepared["package_contents_persisted"] is False


def test_preparation_requires_access_or_portability_ready_identity_context_and_no_holds(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    account, access_request = _account_and_request(db)
    fulfilment = PrivacyFulfilmentStore(db)

    with pytest.raises(ValueError, match="ready for fulfilment"):
        fulfilment.prepare(access_request["id"], prepared_by="Kev")

    case = _ready_case(db, access_request["id"])
    case.set_holds(
        access_request["id"],
        legal_hold=True,
        retention_hold=False,
        reason="Active preservation requirement under review",
    )
    with pytest.raises(ValueError, match="hold blocks"):
        fulfilment.prepare(access_request["id"], prepared_by="Kev")

    deletion = PrivacyEvidenceStore(db).submit_request(
        user_id=account.user_id,
        request_type="deletion",
        detail="Delete my account after review.",
        locale="en-GB",
    )
    _ready_case(db, deletion["id"])
    with pytest.raises(ValueError, match="access or portability"):
        fulfilment.prepare(deletion["id"], prepared_by="Kev")


def test_preparation_integrity_tampering_fails_closed_before_member_delivery(tmp_path):
    db = tmp_path / "privacy.sqlite3"
    account, request_row, prepared, fulfilment = _fulfilled_disclosure(db)
    assert prepared["preparation_evidence_valid"] is True

    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE privacy_fulfilment_packages SET prepared_by='tampered-reviewer' WHERE request_id=?",
            (request_row["id"],),
        )
    with pytest.raises(RuntimeError, match="integrity verification"):
        fulfilment.deliver(request_row["id"], user_id=account.user_id)


def test_member_http_delivery_is_private_no_store_and_owner_preparation_route_fails_closed(monkeypatch, tmp_path):
    db = tmp_path / "privacy.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db))
    account, request_row = _account_and_request(db)
    _ready_case(db, request_row["id"])

    owner_app = FastAPI()
    owner_app.include_router(owner_router)
    owner_client = TestClient(owner_app)
    assert owner_client.post(f"/owner/privacy/cases/{request_row['id']}/prepare-disclosure").status_code == 403
    assert "/owner/privacy/cases/{request_id}/prepare-disclosure" not in owner_app.openapi()["paths"]

    monkeypatch.setattr("aura_music_studio.privacy_fulfilment.owner_authorized", lambda request: True)
    monkeypatch.setattr("aura_music_studio.privacy_fulfilment.owner_actor", lambda: "Mary Ortiz")
    prepared_response = owner_client.post(f"/owner/privacy/cases/{request_row['id']}/prepare-disclosure")
    assert prepared_response.status_code == 200
    prepared = prepared_response.json()["preparation"]
    assert prepared["prepared_by"] == "Mary Ortiz"
    assert prepared_response.json()["automatic_data_action_taken"] is False

    PrivacyCaseStore(db).transition(
        request_row["id"],
        status="fulfilled",
        fulfilment_reference=prepared["fulfilment_reference"],
    )

    member_app = FastAPI()

    @member_app.middleware("http")
    async def bind_member(request: Request, call_next):
        request.state.member = type("Member", (), {"user_id": account.user_id})()
        return await call_next(request)

    member_app.include_router(member_privacy_router)
    member_client = TestClient(member_app)
    response = member_client.get(f"/member/privacy/rights/requests/{request_row['id']}/fulfilment")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"].endswith('.json"')
    assert response.json()["package"]["subject"]["user_id"] == account.user_id


def test_integration_overlay_mounts_member_delivery_and_hides_owner_preparation(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "privacy.sqlite3"))
    from aura_music_studio.creative_version_autopromotion import router as integration_router

    app = FastAPI()
    app.include_router(integration_router)
    paths = app.openapi()["paths"]
    assert "/member/privacy/rights/requests/{request_id}/fulfilment" in paths
    assert "/owner/privacy/cases/{request_id}/prepare-disclosure" not in paths
    client = TestClient(app)
    assert client.post("/owner/privacy/cases/nonexistent/prepare-disclosure").status_code == 403

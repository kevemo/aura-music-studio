from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio.privacy_rights import POLICY_REGISTRY, PrivacyEvidenceStore, router


def test_privacy_request_is_idempotent_and_never_auto_executes(tmp_path):
    store = PrivacyEvidenceStore(tmp_path / "privacy.sqlite3")
    first = store.submit_request(
        user_id="member-a",
        request_type="deletion",
        detail="Please review my account for deletion.",
        locale="en-GB",
    )
    second = store.submit_request(
        user_id="member-a",
        request_type="deletion",
        detail="A duplicate submission must not create a second open request.",
        locale="en-US",
    )
    assert second["id"] == first["id"]
    assert second["status"] == "submitted"

    snapshot = store.snapshot("member-a")
    assert len(snapshot["requests"]) == 1
    assert snapshot["requests"][0]["request_type"] == "deletion"


def test_privacy_evidence_is_member_scoped_and_append_only(tmp_path):
    store = PrivacyEvidenceStore(tmp_path / "privacy.sqlite3")
    a = store.record_policy_decision(
        user_id="member-a",
        policy_key="global_compliance_notice",
        decision="acknowledged",
        locale="en-GB",
    )
    duplicate = store.record_policy_decision(
        user_id="member-a",
        policy_key="global_compliance_notice",
        decision="acknowledged",
        locale="en-GB",
    )
    withdrawn = store.record_policy_decision(
        user_id="member-a",
        policy_key="global_compliance_notice",
        decision="withdrawn",
        locale="en-GB",
    )
    store.record_policy_decision(
        user_id="member-b",
        policy_key="global_compliance_notice",
        decision="acknowledged",
        locale="en-GB",
    )

    assert duplicate["id"] == a["id"]
    assert withdrawn["id"] != a["id"]
    assert withdrawn["policy_version"] == POLICY_REGISTRY["global_compliance_notice"]
    assert len(store.snapshot("member-a")["policy_evidence"]) == 2
    assert len(store.snapshot("member-b")["policy_evidence"]) == 1


def test_routes_bind_to_authenticated_member_and_ignore_spoofed_user_id(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "privacy.sqlite3"))
    app = FastAPI()

    @app.middleware("http")
    async def bind_member(request: Request, call_next):
        request.state.member = SimpleNamespace(user_id="authenticated-member")
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)

    created = client.post(
        "/member/privacy/rights/requests",
        json={
            "request_type": "access",
            "detail": "I want a copy of my personal data.",
            "locale": "es",
            "user_id": "attacker-selected-user",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["request"]["request_type"] == "access"
    assert body["automatic_action_taken"] is False
    assert body["grants_esp_role_or_permission"] is False

    snapshot = PrivacyEvidenceStore(tmp_path / "privacy.sqlite3").snapshot("authenticated-member")
    assert len(snapshot["requests"]) == 1
    assert PrivacyEvidenceStore(tmp_path / "privacy.sqlite3").snapshot("attacker-selected-user")["requests"] == []


def test_member_routes_fail_closed_without_authenticated_context(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "privacy.sqlite3"))
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    denied = client.get("/member/privacy/rights")
    assert denied.status_code == 401


def test_policy_decision_route_records_versioned_evidence_without_role_effect(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "privacy.sqlite3"))
    app = FastAPI()

    @app.middleware("http")
    async def bind_member(request: Request, call_next):
        request.state.member = SimpleNamespace(user_id="member-c")
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        "/member/privacy/policy-decisions",
        json={"policy_key": "ai_transparency", "decision": "acknowledged", "locale": "ar"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"]["policy_version"] == POLICY_REGISTRY["ai_transparency"]
    assert payload["evidence"]["locale"] == "ar"
    assert payload["grants_esp_role_or_permission"] is False
    assert payload["contract_or_legal_certification"] is False

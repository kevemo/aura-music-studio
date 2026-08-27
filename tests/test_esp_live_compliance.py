from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio import esp_live_compliance as compliance
from aura_music_studio.esp_live_compliance import (
    ASSESSMENT_QUESTIONS,
    POLICY_PACK,
    EspComplianceStore,
    member_router,
    owner_router,
)


def _seed_membership(db, *, user_id="creator-1", role="creator", status="active"):
    with sqlite3.connect(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS esp_memberships (
                   user_id TEXT PRIMARY KEY, status TEXT NOT NULL, roles TEXT NOT NULL
               )"""
        )
        con.execute(
            "INSERT OR REPLACE INTO esp_memberships(user_id,status,roles) VALUES (?,?,?)",
            (user_id, status, role),
        )


def _correct_answers():
    return {question["id"]: question["correct"] for question in ASSESSMENT_QUESTIONS}


def _member_app(monkeypatch, db, *, user_id="creator-1"):
    monkeypatch.setenv("LSS_DB_PATH", str(db))
    app = FastAPI()

    @app.middleware("http")
    async def bind_member(request: Request, call_next):
        request.state.member = SimpleNamespace(user_id=user_id)
        return await call_next(request)

    app.include_router(member_router)
    app.include_router(owner_router)
    return app


def test_assessment_has_exactly_40_questions_and_hides_correct_answers(monkeypatch, tmp_path):
    db = tmp_path / "compliance.sqlite3"
    _seed_membership(db)
    client = TestClient(_member_app(monkeypatch, db))
    response = client.get("/esp/compliance/training")
    assert response.status_code == 200
    body = response.json()
    assert body["question_count"] == 40
    assert len(body["questions"]) == 40
    assert all("correct" not in question for question in body["questions"])
    assert body["grants_esp_role_or_permission"] is False


def test_non_esp_member_fails_closed(monkeypatch, tmp_path):
    db = tmp_path / "compliance.sqlite3"
    _seed_membership(db, status="none")
    client = TestClient(_member_app(monkeypatch, db))
    assert client.get("/esp/compliance/training").status_code == 403
    assert client.get("/esp/compliance/readiness").status_code == 403


def test_assessment_requires_all_answers_and_critical_questions(monkeypatch, tmp_path):
    db = tmp_path / "compliance.sqlite3"
    _seed_membership(db)
    store = EspComplianceStore(db)
    answers = _correct_answers()
    answers.pop("q40")
    try:
        store.grade("creator-1", answers)
        raise AssertionError("missing answers should fail")
    except ValueError as exc:
        assert "exactly 40" in str(exc)

    answers = _correct_answers()
    answers["q01"] = "A"
    result = store.grade("creator-1", answers)
    assert result["score"] >= 90
    assert result["critical_passed"] is False
    assert result["passed"] is False
    assert "q01" in result["critical_failed_question_ids"]


def test_readiness_requires_current_policy_assessment_and_owner_verification(tmp_path):
    db = tmp_path / "compliance.sqlite3"
    _seed_membership(db)
    store = EspComplianceStore(db)
    assert store.snapshot("creator-1")["ready_for_esp_compliance_review"] is False

    for key in POLICY_PACK:
        store.record_policy_decision("creator-1", key, "acknowledged", "en-GB")
    passed = store.grade("creator-1", _correct_answers())
    assert passed["passed"] is True
    partially_ready = store.snapshot("creator-1")
    assert partially_ready["policy_acknowledgements_complete"] is True
    assert partially_ready["assessment_passed"] is True
    assert partially_ready["owner_verifications_complete"] is False

    for key in partially_ready["required_owner_verifications"]:
        store.record_owner_verification("creator-1", key, "verified", f"evidence:{key}")
    ready = store.snapshot("creator-1")
    assert ready["ready_for_esp_compliance_review"] is True
    assert ready["tiktok_live_eligibility_verified_by_this_system"] is False
    assert ready["legal_compliance_certified_by_this_system"] is False
    assert ready["grants_esp_role_or_permission"] is False


def test_owner_verification_rejects_url_or_path_and_never_changes_role(tmp_path):
    db = tmp_path / "compliance.sqlite3"
    _seed_membership(db, role="both")
    store = EspComplianceStore(db)
    for bad in ("https://example.com/evidence", "folder/file", r"C:\\secret\\id"):
        try:
            store.record_owner_verification("creator-1", "age_18_plus", "verified", bad)
            raise AssertionError("unsafe reference should fail")
        except ValueError:
            pass
    store.record_owner_verification("creator-1", "age_18_plus", "verified", "verify:age:opaque123")
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT status,roles FROM esp_memberships WHERE user_id='creator-1'").fetchone()
    assert row == ("active", "both")


def test_owner_routes_fail_closed_and_are_hidden_from_openapi(monkeypatch, tmp_path):
    db = tmp_path / "compliance.sqlite3"
    _seed_membership(db)
    app = _member_app(monkeypatch, db)
    client = TestClient(app)
    denied = client.get("/owner/esp/compliance/creator-1")
    assert denied.status_code == 403
    assert "/owner/esp/compliance/{user_id}" not in app.openapi()["paths"]


def test_owner_can_record_bounded_verification_without_automatic_action(monkeypatch, tmp_path):
    db = tmp_path / "compliance.sqlite3"
    _seed_membership(db)
    monkeypatch.setattr(compliance, "owner_authorized", lambda request: True)
    client = TestClient(_member_app(monkeypatch, db))
    response = client.post(
        "/owner/esp/compliance/creator-1/verify",
        json={"check_key": "age_18_plus", "state": "verified", "evidence_ref": "manual:age:check123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["automatic_role_change"] is False
    assert body["automatic_tiktok_action"] is False
    assert body["grants_esp_role_or_permission"] is False


def test_integration_overlay_mounts_member_compliance_routes():
    from aura_music_studio.creative_version_autopromotion import router as integration_router

    app = FastAPI()
    app.include_router(integration_router)
    paths = app.openapi()["paths"]
    assert "/esp/compliance/training" in paths
    assert "/esp/compliance/assessment" in paths
    assert "/esp/compliance/readiness" in paths
    assert "/esp/compliance/policy-decisions" in paths

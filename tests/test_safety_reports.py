from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from aura_music_studio.safety_reports import SafetyReportStore, member_router, owner_router


def test_member_report_is_scoped_and_never_auto_punishes(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    result = store.submit(
        reporter_user_id="member-a", category="harassment", target_type="message",
        target_reference="message:123", detail="Repeated abusive messages.",
        evidence_references=["evidence:one"], immediate_danger=False,
    )
    assert result["report"]["status"] == "submitted"
    assert result["report"]["reporter_user_id"] == "member-a"
    with pytest.raises(KeyError):
        store.get_for_member(result["report"]["id"], "member-b")
    owner = store.owner_get(result["report"]["id"])
    assert owner["automatic_moderation_action_taken"] is False
    assert owner["grants_esp_role_or_permission"] is False
    assert owner["changes_billing_or_membership"] is False
    assert owner["audit_chain_valid"] is True


def test_opaque_refs_reject_urls_and_paths(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    with pytest.raises(ValueError):
        store.submit(
            reporter_user_id="member-a", category="privacy", target_type="content",
            target_reference="https://example.com/private", detail="Private information exposed.",
            evidence_references=[], immediate_danger=False,
        )
    with pytest.raises(ValueError):
        store.submit(
            reporter_user_id="member-a", category="privacy", target_type="content",
            target_reference="content:1", detail="Private information exposed.",
            evidence_references=["../../secret.txt"], immediate_danger=False,
        )


def test_terminal_review_requires_resolution_evidence_and_appeal_is_member_bound(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    report = store.submit(
        reporter_user_id="member-a", category="bullying", target_type="live",
        target_reference="live:abc", detail="Targeted bullying during LIVE.",
        evidence_references=[], immediate_danger=False,
    )["report"]
    with pytest.raises(ValueError):
        store.owner_review(report_id=report["id"], status="resolved", reason="Reviewed")
    resolved = store.owner_review(
        report_id=report["id"], status="resolved", reason="Reviewed against safety policy.",
        resolution_reference="review:case-1",
    )
    assert resolved["report"]["status"] == "resolved"
    appeal = store.appeal(
        report_id=report["id"], appellant_user_id="member-a", reason="Please reconsider the outcome.",
        evidence_references=["evidence:appeal-1"],
    )
    assert appeal["status"] == "submitted"
    duplicate = store.appeal(
        report_id=report["id"], appellant_user_id="member-a", reason="Duplicate open appeal.",
        evidence_references=[],
    )
    assert duplicate["id"] == appeal["id"]
    with pytest.raises(KeyError):
        store.appeal(report_id=report["id"], appellant_user_id="member-b", reason="Spoofed appeal.", evidence_references=[])


def test_owner_review_audit_event_records_bounded_reviewer_actor(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    report = store.submit(
        reporter_user_id="member-a", category="harassment", target_type="message",
        target_reference="message:owner-review", detail="Owner review attribution case.",
        evidence_references=[], immediate_danger=False,
    )["report"]
    result = store.owner_review(
        report_id=report["id"], status="under_review", reason="Mary started review.",
        reviewer_actor="Mary · ESP Owner",
    )
    event = [item for item in result["events"] if item["action"] == "owner_review"][-1]
    assert event["actor_type"] == "owner"
    assert event["data"]["reviewer_actor"] == "Mary · ESP Owner"
    assert event["data"]["automatic_moderation_action_taken"] is False
    with pytest.raises(ValueError, match="Reviewer actor label"):
        store.owner_review(
            report_id=report["id"], status="under_review", reason="Invalid reviewer label.",
            reviewer_actor="x" * 121,
        )


def test_appeal_before_terminal_decision_fails_closed(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    report = store.submit(
        reporter_user_id="member-a", category="fraud_scam", target_type="member",
        target_reference="member:target", detail="Suspected scam solicitation.", evidence_references=[], immediate_danger=False,
    )["report"]
    with pytest.raises(ValueError):
        store.appeal(report_id=report["id"], appellant_user_id="member-a", reason="Too early", evidence_references=[])


def test_member_http_identity_cannot_be_spoofed_and_emergency_notice_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "safety.sqlite3"))
    app = FastAPI()

    @app.middleware("http")
    async def bind_member(request: Request, call_next):
        request.state.member = SimpleNamespace(user_id="authenticated-member")
        return await call_next(request)

    app.include_router(member_router)
    client = TestClient(app)
    response = client.post(
        "/member/safety/reports",
        json={
            "category": "violent_threat", "target_type": "message", "target_reference": "message:9",
            "detail": "A direct threat was made.", "evidence_references": [], "immediate_danger": True,
            "reporter_user_id": "attacker-selected-user",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["reporter_user_id"] == "authenticated-member"
    assert body["automatic_moderation_action_taken"] is False
    assert body["grants_esp_role_or_permission"] is False
    assert "emergency services" in body["emergency_notice"]
    assert SafetyReportStore(tmp_path / "safety.sqlite3").list_for_member("attacker-selected-user") == []


def test_member_routes_fail_closed_without_session_and_owner_routes_fail_closed_without_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "safety.sqlite3"))
    app = FastAPI()
    app.include_router(member_router)
    app.include_router(owner_router)
    client = TestClient(app)
    assert client.get("/member/safety/reports").status_code == 401
    assert client.get("/owner/safety/reports").status_code == 403
    assert "/owner/safety/reports" not in app.openapi()["paths"]


def test_audit_chain_detects_persisted_tampering(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    report = store.submit(
        reporter_user_id="member-a", category="hate", target_type="content",
        target_reference="content:42", detail="Hateful content report.", evidence_references=[], immediate_danger=False,
    )["report"]
    assert store.owner_get(report["id"])["audit_chain_valid"] is True
    with store._connect() as con:
        con.execute("UPDATE safety_report_events SET data_json='{}' WHERE report_id=?", (report["id"],))
    assert store.owner_get(report["id"])["audit_chain_valid"] is False


def test_integration_overlay_mounts_member_and_hidden_owner_safety_routes(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "safety.sqlite3"))
    from aura_music_studio.creative_version_autopromotion import router as integration_router

    app = FastAPI()
    app.include_router(integration_router)
    paths = app.openapi()["paths"]
    assert "/member/safety/reports" in paths
    assert "/member/safety/reports/{report_id}/appeals" in paths
    assert "/owner/safety/reports" not in paths
    client = TestClient(app)
    assert client.get("/owner/safety/reports").status_code == 403
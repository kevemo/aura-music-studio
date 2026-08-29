from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.safety_appeal_review import review_appeal
from aura_music_studio.safety_reports import SafetyReportStore


def _resolved_report_with_appeal(store: SafetyReportStore):
    report = store.submit(
        reporter_user_id="member-a", category="harassment", target_type="message",
        target_reference="message:appeal", detail="Harassing message.", evidence_references=[], immediate_danger=False,
    )["report"]
    store.owner_review(
        report_id=report["id"], status="resolved", reason="Initial review complete.",
        resolution_reference="review:initial",
    )
    appeal = store.appeal(
        report_id=report["id"], appellant_user_id="member-a", reason="Please reconsider.", evidence_references=[]
    )
    return report, appeal


def test_terminal_appeal_requires_evidence_and_does_not_mutate_report(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    report, appeal = _resolved_report_with_appeal(store)
    with pytest.raises(ValueError):
        review_appeal(store, appeal_id=appeal["id"], status="overturned", reason="Reconsidered")
    result = review_appeal(
        store, appeal_id=appeal["id"], status="overturned", reason="Reconsidered with new evidence.",
        resolution_reference="appeal:decision-1",
    )
    assert result["appeal"]["status"] == "overturned"
    assert result["automatic_moderation_action_taken"] is False
    assert result["report_state_automatically_changed"] is False
    assert result["grants_esp_role_or_permission"] is False
    assert result["changes_billing_or_membership"] is False
    assert store.owner_get(report["id"])["report"]["status"] == "resolved"


def test_terminal_appeal_cannot_flip_to_opposite_decision(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    _, appeal = _resolved_report_with_appeal(store)
    review_appeal(
        store, appeal_id=appeal["id"], status="upheld", reason="Original decision supported.",
        resolution_reference="appeal:upheld-1",
    )
    with pytest.raises(ValueError):
        review_appeal(
            store, appeal_id=appeal["id"], status="overturned", reason="Attempt to flip terminal decision.",
            resolution_reference="appeal:flip",
        )


def test_appeal_audit_event_preserves_verified_owner_actor_label(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    report, appeal = _resolved_report_with_appeal(store)
    review_appeal(
        store,
        appeal_id=appeal["id"],
        status="upheld",
        reason="Independent appeal review supports the original decision.",
        resolution_reference="appeal:owner-attribution-1",
        reviewer_actor="Mary · ESP Owner",
    )
    case = store.owner_get(report["id"])
    event = [item for item in case["events"] if item["action"] == "appeal_review"][-1]
    assert event["actor_type"] == "owner"
    assert event["data"]["reviewer_actor"] == "Mary · ESP Owner"
    assert event["data"]["automatic_moderation_action_taken"] is False
    assert event["data"]["report_state_automatically_changed"] is False


def test_reviewer_actor_label_is_bounded(tmp_path):
    store = SafetyReportStore(tmp_path / "safety.sqlite3")
    _, appeal = _resolved_report_with_appeal(store)
    with pytest.raises(ValueError, match="Reviewer actor label"):
        review_appeal(
            store,
            appeal_id=appeal["id"],
            status="under_review",
            reason="Review started.",
            reviewer_actor="x" * 121,
        )


def test_hidden_owner_appeal_route_is_mounted_and_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "safety.sqlite3"))
    from aura_music_studio.creative_version_autopromotion import router as integration_router

    app = FastAPI()
    app.include_router(integration_router)
    assert "/owner/safety/appeals/{appeal_id}/review" not in app.openapi()["paths"]
    client = TestClient(app)
    response = client.post(
        "/owner/safety/appeals/not-a-real-appeal/review",
        json={"status": "under_review", "reason": "Review", "resolution_reference": ""},
    )
    assert response.status_code == 403

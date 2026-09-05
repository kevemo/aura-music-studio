from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_backstage_evidence import (
    DIRECT_BACKSTAGE_ACCESS,
    BackstageEvidenceStore,
    extract_structured_metrics,
    router,
)
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.esp_niche import EspNicheStore
from aura_music_studio.esp_progress import EspProgressStore


def _active(accounts: AccountStore, esp: EspStore, email: str, role: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], role, email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", role, "Owner")
    return user


def _store(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    EspNicheStore(esp)
    assignments = EspAgentAssignmentStore(esp)
    progress = EspProgressStore(esp)
    return accounts, esp, assignments, BackstageEvidenceStore(esp, assignments, progress), progress


def test_no_direct_backstage_access_is_claimed():
    assert DIRECT_BACKSTAGE_ACCESS is False


def test_csv_and_json_exports_extract_only_known_numeric_metrics():
    csv_metrics, csv_status = extract_structured_metrics(
        "live.csv",
        b"Views,Peak Viewers,New Followers,Shares,Private Note\n1250,42,13,7,ignore me\n",
    )
    assert csv_status == "structured_extracted"
    assert csv_metrics == {"views": 1250, "peak_viewers": 42, "new_followers": 13, "shares": 7}

    json_metrics, json_status = extract_structured_metrics(
        "live.json",
        b'{"metrics":{"average_watch_seconds":"73.5","diamonds":"2,400","unknown":"abc"}}',
    )
    assert json_status == "structured_extracted"
    assert json_metrics == {"avg_watch_seconds": 73.5, "diamonds": 2400}


def test_screenshot_requires_visual_review_instead_of_fake_ocr():
    metrics, status = extract_structured_metrics("backstage.png", b"not actually image data")
    assert metrics == {}
    assert status == "visual_review_required"


def test_agent_can_record_evidence_only_for_explicitly_assigned_creator(tmp_path):
    accounts, esp, assignments, evidence, _progress = _store(tmp_path)
    agent = _active(accounts, esp, "agent@example.com", "agent")
    assigned = _active(accounts, esp, "assigned@example.com", "creator")
    unassigned = _active(accounts, esp, "unassigned@example.com", "creator")
    assignments.assign(agent["id"], assigned["id"], actor="Owner")

    row = evidence.record(
        agent["id"], assigned["id"], source_kind="export", source_label="Manage Creator",
        captured_at="2026-08-26T20:00:00+00:00", period_label="Evening LIVE",
        metrics={"views": 1200, "peak_viewers": 55, "new_followers": 9},
        extraction_status="structured_extracted",
    )
    assert row["creator_user_id"] == assigned["id"]
    assert row["direct_backstage_access"] is False
    assert row["metrics"]["views"] == 1200

    with pytest.raises(PermissionError, match="not actively assigned"):
        evidence.record(
            agent["id"], unassigned["id"], source_kind="manual", metrics={"views": 10}
        )


def test_agent_evidence_feeds_existing_creator_progress_guidance(tmp_path):
    accounts, esp, assignments, evidence, progress = _store(tmp_path)
    agent = _active(accounts, esp, "agent2@example.com", "agent")
    creator = _active(accounts, esp, "creator2@example.com", "creator")
    assignments.assign(agent["id"], creator["id"], actor="Owner")

    row = evidence.record(
        agent["id"], creator["id"], source_kind="screenshot",
        period_label="Weekly review", metrics={"duration_minutes": 60, "avg_watch_seconds": 35, "shares": 0},
        extraction_status="manual_metrics",
    )
    assert row["progress_submission_id"]
    summary = progress.summary(creator["id"])
    assert summary["total"] == 1
    assert summary["latest"]["metrics"]["duration_minutes"] == 60
    assert any("opening minute" in item.lower() for item in summary["latest"]["aura_guidance"])


def test_queue_tracks_missing_and_latest_evidence_for_assigned_creators(tmp_path):
    accounts, esp, assignments, evidence, _progress = _store(tmp_path)
    agent = _active(accounts, esp, "agent3@example.com", "agent")
    creator = _active(accounts, esp, "creator3@example.com", "creator")
    assignments.assign(agent["id"], creator["id"], actor="Owner")

    queue = evidence.queue(agent["id"])
    assert len(queue) == 1
    assert queue[0]["freshness"]["status"] == "missing"

    evidence.record(
        agent["id"], creator["id"], source_kind="manual", captured_at="2020-01-01T00:00:00+00:00",
        metrics={"views": 25}, extraction_status="manual_metrics",
    )
    queue = evidence.queue(agent["id"])
    assert queue[0]["freshness"]["status"] == "stale"
    assert queue[0]["latest"]["direct_backstage_access"] is False


def test_router_exposes_only_private_command_center_paths():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/agent/backstage-evidence" in paths
    assert "/command-center/api/agent/backstage-evidence" in paths
    assert "/command-center/api/agent/backstage-evidence/{creator_user_id}" in paths
    assert "/agent/backstage-evidence" not in paths

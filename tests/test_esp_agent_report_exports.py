from __future__ import annotations

import csv
import io

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_roster import AgentRosterStore
from aura_music_studio.esp_backstage_evidence import BackstageEvidenceStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.esp_niche import EspNicheStore
from aura_music_studio.esp_progress import EspProgressStore
import aura_music_studio.esp_agent_development_planner as planner_mod
from aura_music_studio.esp_agent_development_planner import AgentDevelopmentStore
from aura_music_studio.esp_agent_report_exports import AgentReportStore, router


def _active(accounts: AccountStore, esp: EspStore, email: str, role: str):
    signup = accounts.signup(email, email.split("@")[0].title(), "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    _request, token = esp.request_access(user["id"], role, email.split("@")[0], "UK+", "test")
    esp.decide(token, "approve", role, "Owner")
    return user


def _setup(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    esp = EspStore(accounts)
    EspNicheStore(esp)
    agent = _active(accounts, esp, "agent@example.com", "agent")
    creator = _active(accounts, esp, "creator@example.com", "creator")
    other = _active(accounts, esp, "other@example.com", "creator")
    assignments = EspAgentAssignmentStore(esp)
    assignments.assign(agent["id"], creator["id"], actor="Owner")
    roster = AgentRosterStore(esp, assignments)
    progress = EspProgressStore(esp)
    evidence = BackstageEvidenceStore(esp, assignments, progress)
    monkeypatch.setattr(planner_mod, "rosters", roster)
    monkeypatch.setattr(planner_mod, "backstage_evidence", evidence)
    planner = AgentDevelopmentStore(db_path=esp.db_path)
    reports = AgentReportStore(evidence, planner, db_path=esp.db_path)
    return agent, creator, other, evidence, planner, reports


def test_report_pack_rejects_unassigned_creator(tmp_path, monkeypatch):
    agent, _creator, other, _evidence, _planner, reports = _setup(tmp_path, monkeypatch)
    with pytest.raises(PermissionError, match="not actively assigned"):
        reports.build_pack(agent["id"], other["id"], owner=False)


def test_report_pack_contains_trends_and_development_without_private_paths(tmp_path, monkeypatch):
    agent, creator, _other, evidence, planner, reports = _setup(tmp_path, monkeypatch)
    evidence.record(
        agent["id"], creator["id"], owner=False, source_kind="screenshot",
        source_label="TikTok Manage Creator baseline", captured_at="2026-08-20T20:00:00+00:00",
        period_label="Baseline", metrics={"avg_watch_seconds": 40, "shares": 1},
        extraction_status="human_confirmed", upload_name="baseline.png",
        upload_path="/srv/private/esp/evidence/creator/baseline.png", upload_content_type="image/png",
    )
    plan = planner.start_plan(agent["id"], creator["id"], owner=False, objective="Improve retention")
    plan = planner.add_milestone(
        agent["id"], plan["id"], owner=False, horizon_days=30, category="Retention",
        title="Raise average watch time", target_metric="avg_watch_seconds", target_value=65,
    )
    evidence.record(
        agent["id"], creator["id"], owner=False, source_kind="screenshot",
        source_label="TikTok Manage Creator review", captured_at="2026-08-27T00:30:00+00:00",
        period_label="Review", metrics={"avg_watch_seconds": 58, "shares": 4},
        extraction_status="aura_vision_human_confirmed", upload_name="review.png",
        upload_path="/srv/private/esp/evidence/creator/review.png", upload_content_type="image/png",
    )
    planner.add_review(agent["id"], plan["id"], owner=False, notes="Reviewed with creator")

    pack = reports.build_pack(agent["id"], creator["id"], owner=False)
    assert pack["summary"]["evidence_records"] == 2
    assert pack["summary"]["latest_metrics"]["avg_watch_seconds"] == 58
    assert pack["summary"]["latest_trend"]["avg_watch_seconds"]["delta"] == 18
    assert pack["summary"]["development_plans"] == 1
    assert pack["summary"]["open_milestones"] == 1
    assert pack["development_plans"][0]["milestones"][0]["target_value"] == 65
    assert pack["development_plans"][0]["reviews"][0]["metrics"]["avg_watch_seconds"] == 58
    assert pack["boundaries"]["direct_tiktok_backstage_access"] is False
    assert pack["boundaries"]["raw_screenshot_bytes_included"] is False
    assert pack["boundaries"]["server_file_paths_included"] is False
    assert all("upload_path" not in row for row in pack["evidence"])
    assert "/srv/private" not in str(pack)


def test_csv_export_has_explicit_record_types_and_escapes_formula_text(tmp_path, monkeypatch):
    agent, creator, _other, evidence, _planner, reports = _setup(tmp_path, monkeypatch)
    evidence.record(
        agent["id"], creator["id"], owner=False, source_kind="manual",
        source_label="=HYPERLINK(\"https://example.invalid\",\"click\")", period_label="Week 1",
        metrics={"views": 100, "new_followers": 5}, extraction_status="manual_confirmed",
    )
    pack = reports.build_pack(agent["id"], creator["id"], owner=False)
    payload = reports.to_csv(pack)
    rows = list(csv.DictReader(io.StringIO(payload)))
    assert rows[0]["record_type"] == "summary"
    metric_rows = [row for row in rows if row["record_type"] == "evidence_metric"]
    assert {row["metric"] for row in metric_rows} == {"views", "new_followers"}
    assert all(row["title"].startswith("'=") for row in metric_rows)
    assert "upload_path" not in payload


def test_creator_listing_is_limited_to_assigned_creators(tmp_path, monkeypatch):
    agent, creator, other, _evidence, _planner, reports = _setup(tmp_path, monkeypatch)
    rows = reports.list_creators(agent["id"], owner=False)
    assert [row["creator_user_id"] for row in rows] == [creator["id"]]
    assert other["id"] not in {row["creator_user_id"] for row in rows}
    assert rows[0]["freshness"]["status"] == "missing"


def test_report_router_exposes_private_json_csv_and_html_routes():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/api/agent/reports" in paths
    assert "/command-center/api/agent/reports/{creator_user_id}" in paths
    assert "/command-center/agent/reports" in paths
    assert "/command-center/agent/reports/{creator_user_id}" in paths
    assert "/command-center/agent/reports/{creator_user_id}.json" in paths
    assert "/command-center/agent/reports/{creator_user_id}.csv" in paths

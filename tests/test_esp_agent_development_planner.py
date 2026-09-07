from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_agent_roster import AgentRosterStore
from aura_music_studio.esp_backstage_evidence import BackstageEvidenceStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_level_up import EspAgentAssignmentStore
from aura_music_studio.esp_niche import EspNicheStore
from aura_music_studio.esp_progress import EspProgressStore
import aura_music_studio.esp_agent_development_planner as planner_mod
from aura_music_studio.esp_agent_development_planner import AgentDevelopmentStore, HORIZONS, router


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
    return accounts, esp, agent, creator, other, evidence, planner


def test_development_plan_rejects_unassigned_creator(tmp_path, monkeypatch):
    _accounts, _esp, agent, _creator, other, _evidence, planner = _setup(tmp_path, monkeypatch)
    with pytest.raises(PermissionError, match="not actively assigned"):
        planner.start_plan(agent["id"], other["id"], owner=False, objective="Build a consistent LIVE system")


def test_plan_uses_latest_uploaded_evidence_as_baseline(tmp_path, monkeypatch):
    _accounts, _esp, agent, creator, _other, evidence, planner = _setup(tmp_path, monkeypatch)
    row = evidence.record(
        agent["id"], creator["id"], owner=False, source_kind="manual", source_label="Manage Creator screenshot review",
        captured_at="2026-08-26T20:00:00+00:00", period_label="Week 1",
        metrics={"avg_watch_seconds": 42, "duration_minutes": 75, "shares": 0, "new_followers": 0},
        extraction_status="manual_confirmed",
    )
    plan = planner.start_plan(agent["id"], creator["id"], owner=False, objective="Improve retention and consistency")
    assert plan["baseline_evidence_id"] == row["id"]
    assert plan["baseline_metrics"]["avg_watch_seconds"] == 42
    assert plan["baseline_metrics"]["duration_minutes"] == 75
    assert plan["direct_backstage_access"] is False
    assert plan["automatic_penalties"] is False
    categories = {item["category"] for item in plan["focus_suggestions"]}
    assert "Retention" in categories
    assert "Consistency" in categories


def test_milestone_horizons_are_strict_and_capture_baseline(tmp_path, monkeypatch):
    _accounts, _esp, agent, creator, _other, evidence, planner = _setup(tmp_path, monkeypatch)
    evidence.record(
        agent["id"], creator["id"], owner=False, source_kind="export", period_label="Baseline",
        metrics={"avg_watch_seconds": 50}, extraction_status="structured_extracted",
    )
    plan = planner.start_plan(agent["id"], creator["id"], owner=False, objective="Raise repeatable retention")
    with pytest.raises(ValueError, match="7, 30, 60 or 90"):
        planner.add_milestone(
            agent["id"], plan["id"], owner=False, horizon_days=14, category="Retention",
            title="Unsupported horizon", target_metric="avg_watch_seconds", target_value=70,
        )
    plan = planner.add_milestone(
        agent["id"], plan["id"], owner=False, horizon_days=30, category="Retention",
        title="Improve average watch time", detail="Test stronger opening and room resets.",
        target_metric="avg_watch_seconds", target_value=70,
    )
    milestone = plan["milestones"][0]
    assert tuple(HORIZONS) == (7, 30, 60, 90)
    assert milestone["horizon_days"] == 30
    assert milestone["baseline_value"] == 50
    assert milestone["target_value"] == 70


def test_completed_milestone_requires_human_evidence_note(tmp_path, monkeypatch):
    _accounts, _esp, agent, creator, _other, _evidence, planner = _setup(tmp_path, monkeypatch)
    plan = planner.start_plan(agent["id"], creator["id"], owner=False, objective="Build creator routine")
    plan = planner.add_milestone(
        agent["id"], plan["id"], owner=False, horizon_days=7, category="Routine",
        title="Complete agreed weekly LIVE schedule",
    )
    milestone_id = plan["milestones"][0]["id"]
    with pytest.raises(ValueError, match="evidence/review note"):
        planner.set_milestone(agent["id"], milestone_id, owner=False, status="done", evidence_note="")
    updated = planner.set_milestone(
        agent["id"], milestone_id, owner=False, status="done",
        evidence_note="Creator completed the agreed schedule and reviewed the week with mentor.",
    )
    assert updated["completion"] == {"done": 1, "total": 1, "percent": 100.0}
    assert updated["milestones"][0]["completed_at"]


def test_review_snapshots_latest_evidence_without_automatic_action(tmp_path, monkeypatch):
    _accounts, _esp, agent, creator, _other, evidence, planner = _setup(tmp_path, monkeypatch)
    evidence.record(
        agent["id"], creator["id"], owner=False, source_kind="manual", period_label="Baseline",
        metrics={"avg_watch_seconds": 40}, extraction_status="manual_confirmed",
    )
    plan = planner.start_plan(agent["id"], creator["id"], owner=False, objective="Improve retention")
    evidence.record(
        agent["id"], creator["id"], owner=False, source_kind="manual", period_label="Review",
        metrics={"avg_watch_seconds": 65, "shares": 3}, extraction_status="manual_confirmed",
    )
    reviewed = planner.add_review(agent["id"], plan["id"], owner=False, notes="30-day mentoring review")
    assert reviewed["reviews"][0]["metrics"]["avg_watch_seconds"] == 65
    assert reviewed["reviews"][0]["metrics"]["shares"] == 3
    assert reviewed["automatic_penalties"] is False
    assert reviewed["direct_backstage_access"] is False


def test_router_exposes_private_agent_development_paths():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/agent/development" in paths
    assert "/command-center/api/agent/development" in paths
    assert "/command-center/api/agent/development/plans" in paths
    assert "/command-center/api/agent/development/plans/{plan_id}/milestones" in paths
    assert "/command-center/api/agent/development/plans/{plan_id}/reviews" in paths
    assert "/command-center/api/agent/development/milestones/{milestone_id}" in paths

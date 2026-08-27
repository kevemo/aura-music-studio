from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_creator_progress_intelligence import (
    ExperimentCreate,
    ExperimentUpdate,
    ProgressIntelligenceStore,
    router,
)
from aura_music_studio.esp_progress import EspProgressStore


def _stores(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "creator@example.com",
        "Creator",
        "a-very-secure-test-password",
        "base",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    esp_store = EspStore(accounts)
    progress = EspProgressStore(esp_store)
    intelligence = ProgressIntelligenceStore(str(accounts.db_path), progress)
    return accounts, user, progress, intelligence


def _add(progress, user_id, kind, label, **metrics):
    return progress.add(
        user_id,
        kind=kind,
        period_label=label,
        metrics=metrics,
        notes=f"Human notes for {label}",
    )


def test_single_sample_does_not_invent_a_comparison(tmp_path):
    _accounts, user, progress, intelligence = _stores(tmp_path)
    _add(progress, user["id"], "video", "Only post", views=500, completion_rate=40)

    analysis = intelligence.analyse(user["id"], "video")
    assert analysis["samples_available"] == 1
    assert analysis["comparison_window"] == 0
    assert analysis["comparison_available"] is False
    assert analysis["trends"] == []
    assert analysis["recommendations"][0]["title"] == "Build a comparison baseline"
    assert analysis["recommendations"][0]["automatic_activation"] is False


def test_equal_windows_calculate_transparent_direction_and_evidence(tmp_path):
    _accounts, user, progress, intelligence = _stores(tmp_path)
    _add(progress, user["id"], "video", "Old 1", views=100, completion_rate=40, shares=4)
    _add(progress, user["id"], "video", "Old 2", views=120, completion_rate=42, shares=5)
    _add(progress, user["id"], "video", "Recent 1", views=200, completion_rate=50, shares=8)
    _add(progress, user["id"], "video", "Recent 2", views=240, completion_rate=54, shares=10)

    analysis = intelligence.analyse(user["id"], "video")
    assert analysis["comparison_window"] == 2
    assert len(analysis["recent_submission_ids"]) == 2
    assert len(analysis["prior_submission_ids"]) == 2
    trends = {row["metric"]: row for row in analysis["trends"]}
    assert trends["views"]["recent_average"] == 220
    assert trends["views"]["prior_average"] == 110
    assert trends["views"]["percent_change"] == 100
    assert trends["views"]["direction"] == "up"
    assert trends["views"]["evidence_strength"] == "medium"
    assert trends["views"]["normative_judgement"] is False
    assert analysis["trend_interpretation"] == "direction_only_not_automatic_good_or_bad"


def test_missing_metrics_are_not_fabricated_into_trends(tmp_path):
    _accounts, user, progress, intelligence = _stores(tmp_path)
    _add(progress, user["id"], "live", "Old", peak_viewers=25)
    _add(progress, user["id"], "live", "Recent", peak_viewers=30)

    analysis = intelligence.analyse(user["id"], "live")
    trend_metrics = {row["metric"] for row in analysis["trends"]}
    assert trend_metrics == {"peak_viewers"}
    assert "avg_watch_seconds" not in trend_metrics
    assert "diamonds" not in trend_metrics


def test_declining_video_completion_creates_explainable_nonautomatic_test(tmp_path):
    _accounts, user, progress, intelligence = _stores(tmp_path)
    _add(progress, user["id"], "video", "Old 1", completion_rate=55, views=1000)
    _add(progress, user["id"], "video", "Old 2", completion_rate=50, views=900)
    _add(progress, user["id"], "video", "Recent 1", completion_rate=30, views=850)
    _add(progress, user["id"], "video", "Recent 2", completion_rate=28, views=800)

    analysis = intelligence.analyse(user["id"], "video")
    suggestions = {item["focus_metric"]: item for item in analysis["recommendations"]}
    completion = suggestions["completion_rate"]
    assert completion["title"] == "Hook and pacing test"
    assert completion["source"] == "explainable_progress_rules"
    assert completion["automatic_activation"] is False
    assert "Compare completion rate" in completion["success_measure"]


def test_live_arrival_retention_pattern_can_be_explained_without_claiming_causality(tmp_path):
    _accounts, user, progress, intelligence = _stores(tmp_path)
    _add(progress, user["id"], "live", "Old 1", peak_viewers=40, avg_watch_seconds=90)
    _add(progress, user["id"], "live", "Old 2", peak_viewers=42, avg_watch_seconds=88)
    _add(progress, user["id"], "live", "Recent 1", peak_viewers=70, avg_watch_seconds=55)
    _add(progress, user["id"], "live", "Recent 2", peak_viewers=75, avg_watch_seconds=50)

    analysis = intelligence.analyse(user["id"], "live")
    titles = {item["title"] for item in analysis["recommendations"]}
    assert "Opening-minute retention test" in titles
    assert "Arrival-to-retention bridge" in titles
    assert all(item["automatic_activation"] is False for item in analysis["recommendations"])


def test_experiment_captures_baseline_and_requires_human_lifecycle(tmp_path):
    _accounts, user, progress, intelligence = _stores(tmp_path)
    _add(progress, user["id"], "video", "Old", views=500, completion_rate=40)
    _add(progress, user["id"], "video", "Recent", views=650, completion_rate=45)

    experiment = intelligence.create_experiment(
        user["id"],
        ExperimentCreate(
            kind="video",
            focus_metric="completion_rate",
            title="Faster hook test",
            hypothesis="A faster opening may improve completion.",
            action="Use the same topic with a faster first-second hook.",
            success_measure="Compare completion rate after the test window.",
            target_direction="up",
            duration_days=7,
        ),
    )
    assert experiment["status"] == "draft"
    assert experiment["baseline"]["latest_value"] == 45
    assert experiment["baseline"]["samples_available"] == 2

    active = intelligence.update_experiment(
        user["id"], experiment["id"], ExperimentUpdate(status="active")
    )
    assert active["status"] == "active"
    assert active["started_at"]

    with pytest.raises(ValueError, match="human result notes"):
        intelligence.update_experiment(
            user["id"], experiment["id"], ExperimentUpdate(status="completed")
        )

    completed = intelligence.update_experiment(
        user["id"],
        experiment["id"],
        ExperimentUpdate(
            status="completed",
            result_notes="Test completed across the planned posts; hook was kept consistent.",
            observed_value=51,
        ),
    )
    assert completed["status"] == "completed"
    assert completed["observed_value"] == 51
    assert completed["completed_at"]

    with pytest.raises(ValueError, match="locked"):
        intelligence.update_experiment(
            user["id"], experiment["id"], ExperimentUpdate(status="active")
        )


def test_experiments_are_tenant_isolated(tmp_path):
    accounts, user, _progress, intelligence = _stores(tmp_path)
    signup = accounts.signup(
        "other@example.com",
        "Other Creator",
        "another-secure-test-password",
        "base",
    )
    other = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    experiment = intelligence.create_experiment(
        user["id"],
        ExperimentCreate(
            kind="live",
            focus_metric="shares",
            title="Share test",
            hypothesis="One planned segment may increase shares.",
            action="Use one planned shareable segment.",
            success_measure="Compare shares across comparable sessions.",
        ),
    )

    with pytest.raises(KeyError):
        intelligence.experiment(other["id"], experiment["id"])
    with pytest.raises(KeyError):
        intelligence.update_experiment(
            other["id"], experiment["id"], ExperimentUpdate(status="active")
        )
    assert intelligence.experiments(other["id"]) == []


def test_dashboard_declares_control_boundaries(tmp_path):
    _accounts, user, _progress, intelligence = _stores(tmp_path)
    dashboard = intelligence.dashboard(user["id"], "live")
    assert dashboard["automatic_strategy_changes"] is False
    assert dashboard["automatic_experiment_activation"] is False
    assert dashboard["source_of_truth"] == "creator_confirmed_progress_history"


def test_progress_intelligence_routes_are_registered():
    route_methods = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set()) or set()))
        for route in router.routes
    }
    assert any(
        path == "/command-center/api/progress/intelligence" and "GET" in methods
        for path, methods in route_methods
    )
    assert any(
        path == "/command-center/api/progress/intelligence/experiments" and "POST" in methods
        for path, methods in route_methods
    )
    assert any(
        path == "/command-center/api/progress/intelligence/experiments/{experiment_id}" and "PATCH" in methods
        for path, methods in route_methods
    )
    assert any(
        path == "/command-center/progress/intelligence" and "GET" in methods
        for path, methods in route_methods
    )

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _request(user_id: str = "u1"):
    return SimpleNamespace(
        state=SimpleNamespace(member=SimpleNamespace(user_id=user_id, display_name="Creator"))
    )


def _json(value):
    if isinstance(value, dict):
        return value
    return json.loads(value.body.decode("utf-8"))


def _prepare(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_post_show_report as reports
    from aura_music_studio import aura_live_run_engine as runner
    from aura_music_studio import aura_live_runtime_intelligence as runtime
    from aura_music_studio import aura_live_show_control as shows

    db = tmp_path / "live-post-show.sqlite3"
    monkeypatch.setattr(shows, "DB_PATH", db)
    shows._init_schema()
    runner._init_schema()
    runtime._init_schema()

    clock = [datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(shows, "_now", lambda: clock[0].isoformat())
    monkeypatch.setattr(runtime, "_utcnow", lambda: clock[0])
    return shows, runner, runtime, reports, clock


def _ready_plan(shows, *, user_id: str = "u1"):
    request = _request(user_id)
    plan = shows.create_show_plan(shows.ShowPlanCreate(title="Post-show LIVE"), request)
    plan = shows.add_show_segment(
        plan["id"],
        shows.ShowSegmentCreate(
            title="Opening",
            segment_type="intro",
            duration_seconds=120,
            scene_name="Main camera",
            cue_label="PRIVATE OPENING SCRIPT LABEL",
        ),
        request,
        expected_revision=plan["revision"],
    )
    plan = shows.add_show_segment(
        plan["id"],
        shows.ShowSegmentCreate(
            title="Main topic",
            segment_type="talk",
            duration_seconds=60,
            scene_name="Topic scene",
            cue_label="PRIVATE TOPIC SCRIPT LABEL",
        ),
        request,
        expected_revision=plan["revision"],
    )
    return shows.update_show_plan(
        plan["id"],
        shows.ShowPlanUpdate(expected_revision=plan["revision"], status="ready"),
        request,
    )


def _start(runner, plan, *, user_id: str = "u1"):
    return _json(
        runner.start_run(
            runner.LiveRunStart(
                command_id=f"start_report_{user_id}_001",
                plan_id=plan["id"],
                expected_plan_revision=plan["revision"],
            ),
            _request(user_id),
        )
    )


def _command(runner, run, action: str, command_id: str, *, user_id: str = "u1"):
    return _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id=command_id,
                action=action,
                expected_revision=run["revision"],
            ),
            _request(user_id),
        )
    )


def test_completed_report_derives_segment_time_pause_navigation_and_readiness(tmp_path, monkeypatch):
    shows, runner, runtime, reports, clock = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows))
    request = _request()

    clock[0] = datetime(2026, 8, 31, 12, 0, 30, tzinfo=timezone.utc)
    _json(
        runtime.update_readiness(
            run["id"],
            runtime.ReadinessCommand(
                command_id="ready_report_seg2_01",
                ordinal=2,
                expected_revision=0,
                cue_ready=True,
                scene_ready=True,
                assets_ready=True,
            ),
            request,
        )
    )

    clock[0] = datetime(2026, 8, 31, 12, 1, 0, tzinfo=timezone.utc)
    run = _command(runner, run, "next", "cmd_report_next001")
    clock[0] = datetime(2026, 8, 31, 12, 1, 10, tzinfo=timezone.utc)
    run = _command(runner, run, "pause", "cmd_report_pause01")
    clock[0] = datetime(2026, 8, 31, 12, 2, 10, tzinfo=timezone.utc)
    run = _command(runner, run, "resume", "cmd_report_resume1")
    clock[0] = datetime(2026, 8, 31, 12, 2, 50, tzinfo=timezone.utc)
    run = _command(runner, run, "complete", "cmd_report_complete")

    report = _json(reports.get_report(run["id"], request))
    assert report["status"] == "completed"
    assert report["timing"]["wall_seconds"] == 170.0
    assert report["timing"]["active_seconds"] == 110.0
    assert report["timing"]["paused_seconds"] == 60.0
    assert report["timing"]["planned_seconds"] == 180
    assert report["timing"]["active_variance_seconds"] == -70.0
    assert report["segments"][0]["active_seconds"] == 60.0
    assert report["segments"][0]["variance_seconds"] == -60.0
    assert report["segments"][1]["active_seconds"] == 50.0
    assert report["segments"][1]["ready_before_first_entry"] is True
    assert report["readiness"]["prepared_before_entry"] == 1
    assert report["readiness"]["eligible_before_entry"] == 1
    assert report["commands"]["next"] == 1
    assert report["commands"]["pause"] == 1
    assert report["commands"]["resume"] == 1
    assert report["commands"]["complete"] == 1
    assert report["completion"]["finished_on_final_segment"] is True
    assert report["completion"]["segments_visited"] == 2


def test_report_filters_private_cues_and_never_invents_provider_metrics(tmp_path, monkeypatch):
    shows, runner, runtime, reports, clock = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows))
    clock[0] = datetime(2026, 8, 31, 12, 0, 45, tzinfo=timezone.utc)
    run = _command(runner, run, "abort", "cmd_report_abort001")

    report = _json(reports.get_report(run["id"], _request()))
    encoded = json.dumps(report)
    assert report["status"] == "aborted"
    assert report["provider_metrics_available"] is False
    assert report["provider_write_authority"] is False
    assert report["provider_live_controlled"] is False
    assert report["guardian_safeguarding_escalation_preserved"] is True
    assert "PRIVATE OPENING SCRIPT LABEL" not in encoded
    assert "PRIVATE TOPIC SCRIPT LABEL" not in encoded
    assert "cue_label" not in encoded
    assert "viewer_count" not in encoded
    assert "diamond" in report["privacy_boundary"]


def test_active_run_has_no_post_show_report_and_other_tenant_gets_404(tmp_path, monkeypatch):
    shows, runner, runtime, reports, clock = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows))

    with pytest.raises(HTTPException) as active:
        reports.get_report(run["id"], _request())
    assert active.value.status_code == 409

    with pytest.raises(HTTPException) as hidden:
        reports.get_report(run["id"], _request("u2"))
    assert hidden.value.status_code == 404


def test_recent_reports_are_tenant_scoped_and_ended_only(tmp_path, monkeypatch):
    shows, runner, runtime, reports, clock = _prepare(tmp_path, monkeypatch)
    run1 = _start(runner, _ready_plan(shows, user_id="u1"), user_id="u1")
    clock[0] = datetime(2026, 8, 31, 12, 0, 30, tzinfo=timezone.utc)
    _command(runner, run1, "abort", "cmd_report_u1_abort")

    clock[0] = datetime(2026, 8, 31, 12, 1, 0, tzinfo=timezone.utc)
    run2 = _start(runner, _ready_plan(shows, user_id="u2"), user_id="u2")
    clock[0] = datetime(2026, 8, 31, 12, 1, 20, tzinfo=timezone.utc)
    _command(runner, run2, "abort", "cmd_report_u2_abort", user_id="u2")

    listed = _json(reports.list_reports(_request("u1"), limit=20))["reports"]
    assert len(listed) == 1
    assert listed[0]["run_id"] == run1["id"]
    assert run2["id"] not in json.dumps(listed)


def test_post_show_ui_states_operational_privacy_and_provider_truth(tmp_path, monkeypatch):
    shows, runner, runtime, reports, clock = _prepare(tmp_path, monkeypatch)
    page = reports.post_show_page(_request()).body.decode("utf-8")
    assert "audited Aura run-of-show" in page
    assert "does not invent TikTok viewers, likes, gifts, shares or diamonds" in page
    assert "never exposes spoken Auto Cue script text or private cue labels" in page
    assert "no provider write authority is claimed" in page
    assert "LIVE Guardian safeguarding remains independent and preserved" in page
    assert "/live-overlay-studio/live-director" in page
    assert "/live-overlay-studio/show-runner" in page

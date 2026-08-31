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
    from aura_music_studio import aura_live_run_engine as runner
    from aura_music_studio import aura_live_runtime_intelligence as runtime
    from aura_music_studio import aura_live_show_control as shows

    db = tmp_path / "live-runtime.sqlite3"
    monkeypatch.setattr(shows, "DB_PATH", db)
    shows._init_schema()
    runner._init_schema()
    runtime._init_schema()

    clock = [datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(shows, "_now", lambda: clock[0].isoformat())
    monkeypatch.setattr(runtime, "_utcnow", lambda: clock[0])
    return shows, runner, runtime, clock


def _ready_plan(shows, *, user_id: str = "u1"):
    request = _request(user_id)
    plan = shows.create_show_plan(shows.ShowPlanCreate(title="Timed LIVE"), request)
    plan = shows.add_show_segment(
        plan["id"],
        shows.ShowSegmentCreate(
            title="Opening",
            segment_type="intro",
            duration_seconds=120,
            scene_name="Main camera",
            cue_label="Opening private cue",
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
            cue_label="Topic private cue",
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
                command_id=f"start_runtime_{user_id}_001",
                plan_id=plan["id"],
                expected_plan_revision=plan["revision"],
            ),
            _request(user_id),
        )
    )


def test_runtime_timing_is_derived_from_audited_runner_commands(tmp_path, monkeypatch):
    shows, runner, runtime, clock = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows))
    request = _request()

    clock[0] = datetime(2026, 8, 31, 12, 1, 0, tzinfo=timezone.utc)
    first = _json(runtime.get_runtime(run["id"], request))
    assert first["timing"]["total_active_seconds"] == 60.0
    assert first["timing"]["current_segment_active_seconds"] == 60.0
    assert first["timing"]["current_segment_remaining_seconds"] == 60.0
    assert first["timing"]["schedule_drift_seconds"] == 0.0
    assert first["timing"]["schedule_state"] == "on_track"

    clock[0] = datetime(2026, 8, 31, 12, 2, 30, tzinfo=timezone.utc)
    run = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_runtime_next01",
                action="next",
                expected_revision=run["revision"],
            ),
            request,
        )
    )
    clock[0] = datetime(2026, 8, 31, 12, 3, 0, tzinfo=timezone.utc)
    second = _json(runtime.get_runtime(run["id"], request))
    assert second["current_ordinal"] == 2
    assert second["timing"]["total_active_seconds"] == 180.0
    assert second["timing"]["current_segment_active_seconds"] == 30.0
    assert second["timing"]["schedule_drift_seconds"] == 30.0
    assert second["segment_entered_at"] == "2026-08-31T12:02:30+00:00"


def test_pause_time_is_excluded_from_active_timing_and_projection(tmp_path, monkeypatch):
    shows, runner, runtime, clock = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows))
    request = _request()

    clock[0] = datetime(2026, 8, 31, 12, 1, 0, tzinfo=timezone.utc)
    run = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_pause_runtime1",
                action="pause",
                expected_revision=run["revision"],
            ),
            request,
        )
    )
    clock[0] = datetime(2026, 8, 31, 12, 6, 0, tzinfo=timezone.utc)
    paused = _json(runtime.get_runtime(run["id"], request))
    assert paused["timing"]["paused"] is True
    assert paused["timing"]["total_active_seconds"] == 60.0
    assert paused["timing"]["current_segment_active_seconds"] == 60.0
    assert paused["timing"]["current_segment_remaining_seconds"] == 60.0
    assert "Show Runner timing is paused" in paused["guidance"][0]

    run = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_resume_runtime",
                action="resume",
                expected_revision=run["revision"],
            ),
            request,
        )
    )
    clock[0] = datetime(2026, 8, 31, 12, 6, 30, tzinfo=timezone.utc)
    resumed = _json(runtime.get_runtime(run["id"], request))
    assert resumed["timing"]["total_active_seconds"] == 90.0
    assert resumed["timing"]["current_segment_active_seconds"] == 90.0
    assert resumed["timing"]["current_segment_remaining_seconds"] == 30.0


def test_readiness_is_revision_bound_idempotent_audited_and_tenant_scoped(tmp_path, monkeypatch):
    shows, runner, runtime, clock = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows))
    request = _request()

    updated = _json(
        runtime.update_readiness(
            run["id"],
            runtime.ReadinessCommand(
                command_id="ready_runtime_0001",
                ordinal=1,
                expected_revision=0,
                cue_ready=True,
                scene_ready=True,
                assets_ready=False,
            ),
            request,
        )
    )
    current = updated["readiness"]["current"]
    assert current["cue_ready"] is True
    assert current["scene_ready"] is True
    assert current["assets_ready"] is False
    assert current["all_ready"] is False
    assert current["revision"] == 1
    assert updated["duplicate_command"] is False

    duplicate = _json(
        runtime.update_readiness(
            run["id"],
            runtime.ReadinessCommand(
                command_id="ready_runtime_0001",
                ordinal=1,
                expected_revision=0,
                cue_ready=True,
                scene_ready=True,
                assets_ready=False,
            ),
            request,
        )
    )
    assert duplicate["duplicate_command"] is True
    assert duplicate["readiness"]["current"]["revision"] == 1

    with pytest.raises(HTTPException) as collision:
        runtime.update_readiness(
            run["id"],
            runtime.ReadinessCommand(
                command_id="ready_runtime_0001",
                ordinal=1,
                expected_revision=1,
                cue_ready=False,
            ),
            request,
        )
    assert collision.value.status_code == 409

    with pytest.raises(HTTPException) as stale:
        runtime.update_readiness(
            run["id"],
            runtime.ReadinessCommand(
                command_id="ready_runtime_0002",
                ordinal=1,
                expected_revision=0,
                assets_ready=True,
            ),
            request,
        )
    assert stale.value.status_code == 409

    with pytest.raises(HTTPException) as hidden:
        runtime.get_runtime(run["id"], _request("u2"))
    assert hidden.value.status_code == 404

    with runtime._connect() as con:
        audit = con.execute(
            """SELECT ordinal,requested_cue_ready,requested_scene_ready,requested_assets_ready,actor
               FROM live_show_run_readiness_commands
               WHERE user_id='u1' AND run_id=?""",
            (run["id"],),
        ).fetchall()
    assert len(audit) == 1
    assert audit[0]["ordinal"] == 1
    assert audit[0]["requested_cue_ready"] == 1
    assert audit[0]["requested_scene_ready"] == 1
    assert audit[0]["requested_assets_ready"] == 0
    assert audit[0]["actor"] == "member:u1"


def test_overlay_safe_view_excludes_private_cue_and_respects_safe_hold(tmp_path, monkeypatch):
    shows, runner, runtime, clock = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows))
    request = _request()

    full = _json(
        runtime.update_readiness(
            run["id"],
            runtime.ReadinessCommand(
                command_id="ready_overlay_0001",
                ordinal=1,
                expected_revision=0,
                cue_ready=True,
                scene_ready=True,
                assets_ready=True,
            ),
            request,
        )
    )
    assert full["current_segment"]["cue_label"] == "Opening private cue"

    safe = runtime.overlay_safe_payload(full)
    encoded = json.dumps(safe)
    assert "cue_label" not in safe
    assert "Opening private cue" not in encoded
    assert "updated_by" not in encoded
    assert safe["readiness"]["all_ready"] is True
    assert safe["provider_write_authority"] is False
    assert safe["overlay_automation_allowed"] is True

    emergency = _json(
        shows.change_emergency_state(
            shows.EmergencyCommand(
                command_id="cmd_safehold_runtime",
                mode="safe_hold",
                reason="Creator requested safe hold",
                expected_revision=0,
            ),
            request,
        )
    )
    assert emergency["automation_suppressed"] is True
    overlay = _json(runtime.get_overlay_safe_runtime(request))["overlay"]
    assert overlay["emergency_mode"] == "safe_hold"
    assert overlay["automation_suppressed"] is True
    assert overlay["overlay_automation_allowed"] is False
    assert overlay["guardian_safeguarding_escalation_preserved"] is True


def test_completed_run_freezes_runtime_horizon_and_readiness_becomes_read_only(tmp_path, monkeypatch):
    shows, runner, runtime, clock = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows))
    request = _request()

    clock[0] = datetime(2026, 8, 31, 12, 1, 30, tzinfo=timezone.utc)
    completed = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_complete_runtime",
                action="complete",
                expected_revision=run["revision"],
            ),
            request,
        )
    )
    snapshot = _json(runtime.get_runtime(run["id"], request))
    assert snapshot["status"] == "completed"
    assert snapshot["timing"]["total_active_seconds"] == 90.0

    clock[0] = datetime(2026, 8, 31, 13, 0, 0, tzinfo=timezone.utc)
    later = _json(runtime.get_runtime(run["id"], request))
    assert later["timing"]["total_active_seconds"] == 90.0

    with pytest.raises(HTTPException) as closed:
        runtime.update_readiness(
            completed["id"],
            runtime.ReadinessCommand(
                command_id="ready_after_end01",
                ordinal=1,
                expected_revision=0,
                cue_ready=True,
            ),
            request,
        )
    assert closed.value.status_code == 409


def test_live_director_ui_states_privacy_and_provider_boundaries(tmp_path, monkeypatch):
    shows, runner, runtime, clock = _prepare(tmp_path, monkeypatch)
    _start(runner, _ready_plan(shows))
    page = runtime.live_director_page(_request()).body.decode("utf-8")
    assert "derives timing from the audited Show Runner lifecycle" in page
    assert "tracks readiness without storing spoken script text" in page
    assert "does not start, end, mute, kick, battle, invite guests or moderate" in page
    assert "/live-overlay-studio/show-runner" in page
    assert "/live-overlay-studio/prompter" in page
    assert "LIVE Guardian safeguarding" in page

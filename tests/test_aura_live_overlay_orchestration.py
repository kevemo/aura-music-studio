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
    from aura_music_studio import aura_live_overlay_orchestration as orchestration
    from aura_music_studio import aura_live_overlay_studio as overlay_studio
    from aura_music_studio import aura_live_run_engine as runner
    from aura_music_studio import aura_live_runtime_intelligence as runtime
    from aura_music_studio import aura_live_show_control as shows

    show_db = tmp_path / "live-orchestration.sqlite3"
    overlay_db = tmp_path / "live-overlay.sqlite3"
    monkeypatch.setattr(shows, "DB_PATH", show_db)
    monkeypatch.setattr(overlay_studio, "DB_PATH", overlay_db)
    shows._init_schema()
    runner._init_schema()
    runtime._init_schema()
    orchestration._init_schema()
    overlay_studio._init_schema()

    clock = [datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(shows, "_now", lambda: clock[0].isoformat())
    monkeypatch.setattr(runtime, "_utcnow", lambda: clock[0])
    monkeypatch.setattr(orchestration, "_utcnow", lambda: clock[0])
    return shows, runner, runtime, orchestration, overlay_studio, clock


def _ready_plan(shows, *, user_id: str = "u1"):
    request = _request(user_id)
    plan = shows.create_show_plan(shows.ShowPlanCreate(title="Orchestrated LIVE"), request)
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
            duration_seconds=180,
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
                command_id=f"start_orchestration_{user_id}_001",
                plan_id=plan["id"],
                expected_plan_revision=plan["revision"],
            ),
            _request(user_id),
        )
    )


def test_orchestration_profile_is_revision_bound_idempotent_and_audited(tmp_path, monkeypatch):
    shows, runner, runtime, orchestration, overlay_studio, clock = _prepare(tmp_path, monkeypatch)
    request = _request()

    initial = _json(orchestration.get_orchestration(request))
    assert initial["active"] is False
    assert initial["profile"]["revision"] == 1
    assert initial["provider_write_authority"] is False

    body = orchestration.OrchestrationProfilePatch(
        command_id="orch_settings_0001",
        expected_revision=1,
        show_scene_name=True,
        show_schedule_status=True,
        countdown_warning_seconds=45,
    )
    updated = _json(orchestration.update_orchestration(body, request))
    assert updated["profile"]["revision"] == 2
    assert updated["profile"]["show_scene_name"] is True
    assert updated["profile"]["show_schedule_status"] is True
    assert updated["profile"]["countdown_warning_seconds"] == 45
    assert updated["duplicate_command"] is False

    duplicate = _json(orchestration.update_orchestration(body, request))
    assert duplicate["duplicate_command"] is True
    assert duplicate["profile"]["revision"] == 2

    with pytest.raises(HTTPException) as collision:
        orchestration.update_orchestration(
            orchestration.OrchestrationProfilePatch(
                command_id="orch_settings_0001",
                expected_revision=2,
                show_scene_name=False,
            ),
            request,
        )
    assert collision.value.status_code == 409

    with pytest.raises(HTTPException) as stale:
        orchestration.update_orchestration(
            orchestration.OrchestrationProfilePatch(
                command_id="orch_settings_0002",
                expected_revision=1,
                show_timer=False,
            ),
            request,
        )
    assert stale.value.status_code == 409

    with orchestration._connect() as con:
        audit = con.execute(
            """SELECT resulting_revision,actor FROM live_show_overlay_orchestration_commands
               WHERE user_id='u1'"""
        ).fetchall()
    assert len(audit) == 1
    assert audit[0]["resulting_revision"] == 2
    assert audit[0]["actor"] == "member:u1"


def test_source_pulse_is_privacy_filtered_and_public_triggers_are_delivered_once(tmp_path, monkeypatch):
    shows, runner, runtime, orchestration, overlay_studio, clock = _prepare(tmp_path, monkeypatch)
    _, token = overlay_studio._ensure_profile("u1")
    assert token
    run = _start(runner, _ready_plan(shows))

    first = _json(orchestration.source_orchestration_pulse(token))
    encoded = json.dumps(first)
    assert first["active"] is True
    assert first["suppressed"] is False
    assert first["presentation"]["visible"] is True
    assert first["presentation"]["segment_title"] == "Opening"
    assert first["presentation"]["remaining_seconds"] == 120.0
    assert first["triggers"] == [{"kind": "segment_entered", "ordinal": 1}]
    assert first["provider_write_authority"] is False
    assert first["provider_live_controlled"] is False
    assert "Opening private cue" not in encoded
    assert "cue_label" not in encoded
    assert '"readiness"' not in encoded
    assert "updated_by" not in encoded
    assert '"u1"' not in encoded

    second = _json(orchestration.source_orchestration_pulse(token))
    assert second["triggers"] == []

    clock[0] = datetime(2026, 8, 31, 12, 1, 1, tzinfo=timezone.utc)
    final_minute = _json(orchestration.source_orchestration_pulse(token))
    assert final_minute["presentation"]["remaining_seconds"] == 59.0
    assert final_minute["triggers"] == [{"kind": "final_minute", "ordinal": 1}]

    again = _json(orchestration.source_orchestration_pulse(token))
    assert again["triggers"] == []


def test_segment_transition_drives_new_public_card_without_provider_control(tmp_path, monkeypatch):
    shows, runner, runtime, orchestration, overlay_studio, clock = _prepare(tmp_path, monkeypatch)
    _, token = overlay_studio._ensure_profile("u1")
    run = _start(runner, _ready_plan(shows))
    _json(orchestration.source_orchestration_pulse(token))

    clock[0] = datetime(2026, 8, 31, 12, 0, 30, tzinfo=timezone.utc)
    run = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_orch_next_0001",
                action="next",
                expected_revision=run["revision"],
            ),
            _request(),
        )
    )
    transitioned = _json(orchestration.source_orchestration_pulse(token))
    assert transitioned["presentation"]["segment_title"] == "Main topic"
    assert transitioned["presentation"]["remaining_seconds"] == 180.0
    assert transitioned["triggers"] == [{"kind": "segment_entered", "ordinal": 2}]
    assert transitioned["provider_write_authority"] is False
    assert transitioned["provider_live_controlled"] is False


def test_operational_overrun_and_drift_stay_private(tmp_path, monkeypatch):
    shows, runner, runtime, orchestration, overlay_studio, clock = _prepare(tmp_path, monkeypatch)
    _, token = overlay_studio._ensure_profile("u1")
    _start(runner, _ready_plan(shows))
    _json(orchestration.source_orchestration_pulse(token))

    clock[0] = datetime(2026, 8, 31, 12, 5, 0, tzinfo=timezone.utc)
    private = _json(orchestration.member_orchestration_pulse(_request()))
    kinds = {item["kind"] for item in private["recent_triggers"]}
    assert "overrun" in kinds
    assert "drift_behind" in kinds
    assert private["timing"]["overrun_seconds"] == 180.0
    assert private["timing"]["schedule_drift_seconds"] == 180.0

    public = _json(orchestration.source_orchestration_pulse(token))
    assert all(item["kind"] in {"segment_entered", "final_minute"} for item in public["triggers"])
    assert "overrun" not in json.dumps(public)
    assert "drift_behind" not in json.dumps(public)


def test_safe_hold_suppresses_runtime_visual_automation_and_trigger_creation(tmp_path, monkeypatch):
    shows, runner, runtime, orchestration, overlay_studio, clock = _prepare(tmp_path, monkeypatch)
    _, token = overlay_studio._ensure_profile("u1")
    _start(runner, _ready_plan(shows))
    request = _request()

    emergency = _json(
        shows.change_emergency_state(
            shows.EmergencyCommand(
                command_id="cmd_orch_safehold01",
                mode="safe_hold",
                reason="Creator requested safe hold",
                expected_revision=0,
            ),
            request,
        )
    )
    assert emergency["automation_suppressed"] is True

    public = _json(orchestration.source_orchestration_pulse(token))
    assert public["active"] is True
    assert public["suppressed"] is True
    assert public["presentation"]["visible"] is False
    assert public["triggers"] == []
    assert public["guardian_safeguarding_escalation_preserved"] is True

    private = _json(orchestration.member_orchestration_pulse(request))
    assert private["recent_triggers"] == []
    assert private["provider_write_authority"] is False
    assert private["manual_provider_actions_required"] is True


def test_source_tokens_are_tenant_scoped_and_invalid_tokens_fail_closed(tmp_path, monkeypatch):
    shows, runner, runtime, orchestration, overlay_studio, clock = _prepare(tmp_path, monkeypatch)
    _, token1 = overlay_studio._ensure_profile("u1")
    _, token2 = overlay_studio._ensure_profile("u2")
    _start(runner, _ready_plan(shows, user_id="u1"), user_id="u1")

    one = _json(orchestration.source_orchestration_pulse(token1))
    two = _json(orchestration.source_orchestration_pulse(token2))
    assert one["active"] is True
    assert two["active"] is False

    with pytest.raises(HTTPException) as invalid:
        orchestration.source_orchestration_pulse("not-a-valid-source-token")
    assert invalid.value.status_code == 404


def test_orchestrated_source_wraps_existing_overlay_and_control_page_states_boundaries(tmp_path, monkeypatch):
    shows, runner, runtime, orchestration, overlay_studio, clock = _prepare(tmp_path, monkeypatch)
    _, token = overlay_studio._ensure_profile("u1")

    source_page = orchestration.orchestrated_overlay_source(token).body.decode("utf-8")
    assert f'/live-overlay/source/{token}' in source_page
    assert "/orchestration/pulse" in source_page
    assert "iframe" in source_page
    assert "cue_label" not in source_page
    assert "Opening private cue" not in source_page

    control_page = orchestration.orchestration_page(_request()).body.decode("utf-8")
    assert "one-source setup" in control_page.lower()
    assert "cannot start/end LIVE" in control_page
    assert "mute/kick viewers" in control_page
    assert "Safe Hold/Automation Pause suppresses" in control_page
    assert "/live-overlay-studio/live-director" in control_page
    assert "/live-overlay-studio/show-runner" in control_page

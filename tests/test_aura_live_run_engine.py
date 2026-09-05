from __future__ import annotations

import json
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
    from aura_music_studio import aura_live_show_control as shows

    db = tmp_path / "live-run.sqlite3"
    monkeypatch.setattr(shows, "DB_PATH", db)
    shows._init_schema()
    runner._init_schema()
    return shows, runner


def _ready_plan(shows, user_id: str = "u1", *, titles=("Intro", "Main", "Outro")):
    request = _request(user_id)
    plan = shows.create_show_plan(shows.ShowPlanCreate(title="Friday LIVE"), request)
    for index, title in enumerate(titles, start=1):
        plan = shows.add_show_segment(
            plan["id"],
            shows.ShowSegmentCreate(
                title=title,
                segment_type="intro" if index == 1 else "talk",
                duration_seconds=60 * index,
                scene_name=f"Scene {index}",
                cue_label=f"Cue {index}",
            ),
            request,
            expected_revision=plan["revision"],
        )
    return shows.update_show_plan(
        plan["id"],
        shows.ShowPlanUpdate(expected_revision=plan["revision"], status="ready"),
        request,
    )


def _start(runner, plan, user_id: str = "u1", command_id: str = "start_command_0001"):
    return _json(
        runner.start_run(
            runner.LiveRunStart(
                command_id=command_id,
                plan_id=plan["id"],
                expected_plan_revision=plan["revision"],
            ),
            _request(user_id),
        )
    )


def test_start_requires_ready_plan_and_snapshots_structure_without_script(tmp_path, monkeypatch):
    shows, runner = _prepare(tmp_path, monkeypatch)
    request = _request()

    draft = shows.create_show_plan(shows.ShowPlanCreate(title="Draft"), request)
    draft = shows.add_show_segment(
        draft["id"],
        shows.ShowSegmentCreate(title="Opening", cue_label="Private cue label"),
        request,
        expected_revision=draft["revision"],
    )
    with pytest.raises(HTTPException) as not_ready:
        runner.start_run(
            runner.LiveRunStart(
                command_id="start_draft_0001",
                plan_id=draft["id"],
                expected_plan_revision=draft["revision"],
            ),
            request,
        )
    assert not_ready.value.status_code == 409

    plan = _ready_plan(shows)
    run = _start(runner, plan)
    assert run["status"] == "running"
    assert run["current_ordinal"] == 1
    assert [item["title"] for item in run["segments"]] == ["Intro", "Main", "Outro"]
    assert run["provider_write_authority"] is False
    assert run["provider_live_started"] is False
    assert run["provider_live_ended"] is False
    assert run["guardian_safeguarding_escalation_preserved"] is True

    original_first = plan["segments"][0]
    changed = shows.update_show_segment(
        plan["id"],
        original_first["id"],
        shows.ShowSegmentUpdate(expected_revision=plan["revision"], title="Changed after start"),
        request,
    )
    assert changed["segments"][0]["title"] == "Changed after start"
    persisted = _json(runner.get_run(run["id"], request))
    assert persisted["segments"][0]["title"] == "Intro"

    with runner._connect() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(live_show_run_segments)").fetchall()}
    assert "script" not in columns
    assert "script_text" not in columns


def test_start_is_tenant_scoped_idempotent_and_only_one_active_run_is_allowed(tmp_path, monkeypatch):
    shows, runner = _prepare(tmp_path, monkeypatch)
    plan = _ready_plan(shows)

    first = _start(runner, plan, command_id="start_once_00001")
    duplicate = _start(runner, plan, command_id="start_once_00001")
    assert duplicate["id"] == first["id"]
    assert duplicate["duplicate_command"] is True

    second_plan = _ready_plan(shows, titles=("Second intro",))
    with pytest.raises(HTTPException) as already_active:
        _start(runner, second_plan, command_id="start_second_001")
    assert already_active.value.status_code == 409

    with pytest.raises(HTTPException) as hidden:
        runner.get_run(first["id"], _request("u2"))
    assert hidden.value.status_code == 404

    other_plan = _ready_plan(shows, "u2", titles=("Other",))
    other = _start(runner, other_plan, "u2", "start_other_0001")
    assert other["id"] != first["id"]


def test_commands_are_revision_bound_idempotent_audited_and_terminal(tmp_path, monkeypatch):
    shows, runner = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows))
    request = _request()

    next_run = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_next_000001", action="next", expected_revision=run["revision"]
            ),
            request,
        )
    )
    assert next_run["current_ordinal"] == 2
    assert next_run["revision"] == 2

    duplicate = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_next_000001", action="next", expected_revision=run["revision"]
            ),
            request,
        )
    )
    assert duplicate["duplicate_command"] is True
    assert duplicate["current_ordinal"] == 2
    assert duplicate["revision"] == 2

    with pytest.raises(HTTPException) as collision:
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_next_000001", action="previous", expected_revision=2
            ),
            request,
        )
    assert collision.value.status_code == 409

    with pytest.raises(HTTPException) as stale:
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_stale_00001", action="next", expected_revision=1
            ),
            request,
        )
    assert stale.value.status_code == 409

    paused = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_pause_00001", action="pause", expected_revision=2
            ),
            request,
        )
    )
    assert paused["status"] == "paused"

    jumped = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_jump_000001", action="jump", ordinal=3, expected_revision=3
            ),
            request,
        )
    )
    assert jumped["status"] == "paused"
    assert jumped["current_ordinal"] == 3

    resumed = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_resume_0001", action="resume", expected_revision=4
            ),
            request,
        )
    )
    completed = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_complete_001", action="complete", expected_revision=resumed["revision"]
            ),
            request,
        )
    )
    assert completed["status"] == "completed"
    assert completed["ended_at"]
    assert _json(runner.get_active_run(request))["run"] is None

    with pytest.raises(HTTPException) as terminal:
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_after_end_01", action="previous", expected_revision=completed["revision"]
            ),
            request,
        )
    assert terminal.value.status_code == 409

    with runner._connect() as con:
        audit = con.execute(
            "SELECT action,previous_status,resulting_status,actor FROM live_show_run_commands "
            "WHERE user_id='u1' AND run_id=? ORDER BY resulting_revision",
            (run["id"],),
        ).fetchall()
    assert [row["action"] for row in audit] == ["next", "pause", "jump", "resume", "complete"]
    assert audit[-1]["actor"] == "member:u1"


def test_safe_hold_suppresses_automation_but_preserves_manual_runner_and_guardian(tmp_path, monkeypatch):
    shows, runner = _prepare(tmp_path, monkeypatch)
    run = _start(runner, _ready_plan(shows, titles=("One", "Two")))
    request = _request()

    emergency = _json(
        shows.change_emergency_state(
            shows.EmergencyCommand(
                command_id="cmd_safehold_run1",
                mode="safe_hold",
                reason="Creator requested safe hold",
                expected_revision=0,
            ),
            request,
        )
    )
    assert emergency["automation_suppressed"] is True

    snapshot = _json(runner.get_run(run["id"], request))
    assert snapshot["emergency_mode"] == "safe_hold"
    assert snapshot["automation_suppressed"] is True
    assert snapshot["guardian_safeguarding_escalation_preserved"] is True

    advanced = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_manual_next1", action="next", expected_revision=snapshot["revision"]
            ),
            request,
        )
    )
    assert advanced["current_ordinal"] == 2
    assert advanced["automation_suppressed"] is True
    assert advanced["provider_write_authority"] is False


def test_abort_releases_active_slot_and_runner_ui_states_provider_boundary(tmp_path, monkeypatch):
    shows, runner = _prepare(tmp_path, monkeypatch)
    plan = _ready_plan(shows, titles=("Only",))
    run = _start(runner, plan)
    request = _request()

    aborted = _json(
        runner.command_run(
            run["id"],
            runner.LiveRunCommand(
                command_id="cmd_abort_00001", action="abort", expected_revision=run["revision"]
            ),
            request,
        )
    )
    assert aborted["status"] == "aborted"
    assert _json(runner.get_active_run(request))["run"] is None

    restarted = _start(runner, plan, command_id="start_after_abort")
    assert restarted["id"] != run["id"]

    page = runner.show_runner_page(request).body.decode("utf-8")
    assert "does not start or end TikTok LIVE" in page
    assert "Spoken Auto Cue script text remains browser-local" in page
    assert "/live-overlay-studio/prompter" in page
    assert "LIVE Guardian safeguarding remains independent and preserved" in page

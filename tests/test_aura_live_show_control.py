from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _prepare(tmp_path, monkeypatch):
    from aura_music_studio import aura_live_show_control as control

    db = tmp_path / "show-control.sqlite3"
    monkeypatch.setattr(control, "DB_PATH", db)
    control._init_schema()
    return control


def _request(user_id: str = "u1"):
    return SimpleNamespace(
        state=SimpleNamespace(member=SimpleNamespace(user_id=user_id, display_name="Creator"))
    )


def _json(response):
    if isinstance(response, dict):
        return response
    return json.loads(response.body.decode("utf-8"))


def test_show_builder_persists_structure_but_has_no_server_side_script_field(tmp_path, monkeypatch):
    control = _prepare(tmp_path, monkeypatch)
    plan = control.create_show_plan(control.ShowPlanCreate(title="Friday LIVE"), _request())
    plan = control.add_show_segment(
        plan["id"],
        control.ShowSegmentCreate(
            title="Opening welcome",
            segment_type="intro",
            duration_seconds=180,
            scene_name="Main camera",
            cue_label="Opening welcome cue",
        ),
        _request(),
        expected_revision=plan["revision"],
    )

    assert plan["segments"][0]["cue_label"] == "Opening welcome cue"
    with control._connect() as con:
        segment_columns = {row[1] for row in con.execute("PRAGMA table_info(live_show_segments)").fetchall()}
    assert "script" not in segment_columns
    assert "script_text" not in segment_columns

    page = control.show_builder_page(_request())
    text = page.body.decode("utf-8")
    assert "actual spoken wording only in Auto Cue" in text
    assert "/live-overlay-studio/prompter" in text
    assert "never written to this database" in text


def test_show_plans_are_tenant_scoped_and_stale_revisions_fail_closed(tmp_path, monkeypatch):
    control = _prepare(tmp_path, monkeypatch)
    created = control.create_show_plan(control.ShowPlanCreate(title="My LIVE"), _request("u1"))

    with pytest.raises(HTTPException) as hidden:
        control.get_show_plan(created["id"], _request("u2"))
    assert hidden.value.status_code == 404

    updated = control.update_show_plan(
        created["id"],
        control.ShowPlanUpdate(expected_revision=created["revision"], description="Run of show"),
        _request("u1"),
    )
    assert updated["revision"] == created["revision"] + 1

    with pytest.raises(HTTPException) as stale:
        control.update_show_plan(
            created["id"],
            control.ShowPlanUpdate(expected_revision=created["revision"], status="ready"),
            _request("u1"),
        )
    assert stale.value.status_code == 409


def test_segment_reorder_and_delete_remain_contiguous_under_unique_ordinal_constraint(tmp_path, monkeypatch):
    control = _prepare(tmp_path, monkeypatch)
    plan = control.create_show_plan(control.ShowPlanCreate(title="Ordered LIVE"), _request())
    for title in ("One", "Two", "Three"):
        plan = control.add_show_segment(
            plan["id"],
            control.ShowSegmentCreate(title=title, duration_seconds=60),
            _request(),
            expected_revision=plan["revision"],
        )

    third = next(item for item in plan["segments"] if item["title"] == "Three")
    plan = control.update_show_segment(
        plan["id"],
        third["id"],
        control.ShowSegmentUpdate(expected_revision=plan["revision"], ordinal=1),
        _request(),
    )
    assert [item["title"] for item in plan["segments"]] == ["Three", "One", "Two"]
    assert [item["ordinal"] for item in plan["segments"]] == [1, 2, 3]

    first = plan["segments"][0]
    plan = control.delete_show_segment(
        plan["id"], first["id"], _request(), expected_revision=plan["revision"]
    )
    assert [item["ordinal"] for item in plan["segments"]] == [1, 2]
    assert [item["title"] for item in plan["segments"]] == ["One", "Two"]


def test_emergency_commands_are_tenant_scoped_idempotent_audited_and_truthful(tmp_path, monkeypatch):
    control = _prepare(tmp_path, monkeypatch)
    request = _request("u1")

    first = _json(
        control.change_emergency_state(
            control.EmergencyCommand(
                command_id="cmd_safehold_0001",
                mode="safe_hold",
                reason="Creator requested safe hold",
                expected_revision=0,
            ),
            request,
        )
    )
    assert first["mode"] == "safe_hold"
    assert first["overlay_safe_hold"] is True
    assert first["automation_suppressed"] is True
    assert first["provider_write_authority"] is False
    assert first["provider_live_ended"] is False
    assert first["guardian_safeguarding_escalation_preserved"] is True
    assert first["duplicate_command"] is False

    duplicate = _json(
        control.change_emergency_state(
            control.EmergencyCommand(
                command_id="cmd_safehold_0001",
                mode="safe_hold",
                reason="same command retry",
                expected_revision=0,
            ),
            request,
        )
    )
    assert duplicate["duplicate_command"] is True
    assert duplicate["revision"] == first["revision"]

    with pytest.raises(HTTPException) as collision:
        control.change_emergency_state(
            control.EmergencyCommand(
                command_id="cmd_safehold_0001",
                mode="normal",
                expected_revision=first["revision"],
            ),
            request,
        )
    assert collision.value.status_code == 409

    with pytest.raises(HTTPException) as stale:
        control.change_emergency_state(
            control.EmergencyCommand(
                command_id="cmd_pause_0002",
                mode="automation_pause",
                expected_revision=0,
            ),
            request,
        )
    assert stale.value.status_code == 409

    other = control.emergency_snapshot("u2")
    assert other["mode"] == "normal"
    assert other["revision"] == 0

    with control._connect() as con:
        audit = con.execute(
            "SELECT requested_mode,previous_mode,actor FROM live_overlay_emergency_commands WHERE user_id='u1'"
        ).fetchall()
    assert len(audit) == 1
    assert audit[0]["requested_mode"] == "safe_hold"
    assert audit[0]["previous_mode"] == "normal"
    assert audit[0]["actor"] == "member:u1"
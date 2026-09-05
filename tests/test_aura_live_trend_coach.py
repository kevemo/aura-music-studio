from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio import aura_live_trend_coach as trends


def _request(user_id: str = "u1"):
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(user_id=user_id, display_name="Creator")))


def _report(
    run_id: str,
    *,
    status: str = "completed",
    variance: float = 60.0,
    paused: float = 10.0,
    ready: int = 1,
    eligible: int = 1,
    safe_hold: int = 0,
    previous: int = 0,
    jump: int = 0,
    segment_variance: float = 0.0,
):
    return {
        "run_id": run_id,
        "plan_id": f"plan-{run_id}",
        "plan_revision": 1,
        "title": f"LIVE {run_id}",
        "status": status,
        "started_at": "2026-09-01T01:00:00+00:00",
        "ended_at": f"2026-09-01T01:0{run_id[-1] if run_id[-1].isdigit() else '1'}:00+00:00",
        "timing": {
            "planned_seconds": 600.0,
            "active_seconds": 600.0 + variance,
            "paused_seconds": paused,
            "wall_seconds": 600.0 + variance + paused,
            "active_variance_seconds": variance,
        },
        "completion": {
            "ended_on_ordinal": 2,
            "final_planned_ordinal": 2,
            "finished_on_final_segment": status == "completed",
            "segments_visited": 2,
            "segments_planned": 2,
        },
        "commands": {"previous": previous, "jump": jump, "next": 1},
        "readiness": {
            "prepared_before_entry": ready,
            "eligible_before_entry": eligible,
            "updates": eligible,
        },
        "emergency_controls": {
            "total": safe_hold,
            "safe_hold": safe_hold,
            "automation_pause": 0,
            "normal": 0,
            "scope": "Aura-local emergency controls only",
        },
        "segments": [
            {
                "ordinal": 1,
                "title": "Opening",
                "segment_type": "intro",
                "scene_name": "Main",
                "planned_seconds": 120,
                "active_seconds": 120 + segment_variance,
                "variance_seconds": segment_variance,
                "visit_count": 1,
                "first_entered_at": "2026-09-01T01:00:00+00:00",
                "ready_before_first_entry": None,
                "final_readiness": {},
            },
            {
                "ordinal": 2,
                "title": "Main Topic",
                "segment_type": "talk",
                "scene_name": "Topic",
                "planned_seconds": 480,
                "active_seconds": 480,
                "variance_seconds": 0,
                "visit_count": 1,
                "first_entered_at": "2026-09-01T01:02:00+00:00",
                "ready_before_first_entry": bool(ready),
                "final_readiness": {},
            },
        ],
        "provider_metrics_available": False,
        "metrics_scope": "Aura Command Center run-of-show operations only",
        "provider_write_authority": False,
        "provider_live_controlled": False,
        "guardian_safeguarding_escalation_preserved": True,
        "privacy_boundary": "PRIVATE CUE CONTENT MUST NOT PROPAGATE",
        "guidance": [],
    }


def test_build_trends_aggregates_audited_sessions_and_latest_comparison():
    latest = _report("r2", variance=60, paused=20, ready=1, eligible=1, previous=0)
    prior = _report("r1", variance=240, paused=80, ready=0, eligible=1, previous=2)
    payload = trends.build_trends([latest, prior])

    assert payload["sessions"] == {"count": 2, "completed": 2, "aborted": 0, "completion_rate": 1.0}
    assert payload["timing"]["average_active_variance_seconds"] == 150.0
    assert payload["timing"]["average_absolute_active_variance_seconds"] == 150.0
    assert payload["readiness"]["rate"] == 0.5
    assert payload["controls"]["rework_commands_total"] == 2
    assert payload["latest"]["run_id"] == "r2"
    assert payload["latest_vs_prior"]["absolute_variance_improved"] is True
    assert payload["latest_vs_prior"]["paused_seconds_delta"] == -60.0
    assert payload["latest_vs_prior"]["readiness_rate_delta"] == 1.0
    assert payload["latest_vs_prior"]["rework_delta"] == -2


def test_recurring_segment_overruns_require_two_audited_sessions():
    payload = trends.build_trends(
        [
            _report("r3", segment_variance=190),
            _report("r2", segment_variance=160),
            _report("r1", segment_variance=20),
        ]
    )
    patterns = payload["recurring_segment_overruns"]
    assert len(patterns) == 1
    assert patterns[0]["title"] == "Opening"
    assert patterns[0]["segment_type"] == "intro"
    assert patterns[0]["sessions_seen"] == 3
    assert patterns[0]["overrun_sessions"] == 2
    assert patterns[0]["overrun_rate"] == pytest.approx(2 / 3, abs=0.0001)


def test_trend_payload_preserves_provider_truth_guardian_and_private_cue_boundary():
    payload = trends.build_trends([_report("r1", safe_hold=1)])
    encoded = json.dumps(payload)

    assert payload["provider_metrics_available"] is False
    assert payload["provider_write_authority"] is False
    assert payload["provider_live_controlled"] is False
    assert payload["guardian_safeguarding_escalation_preserved"] is True
    assert "TikTok viewers, gifts, likes, shares, coins or diamonds" in payload["privacy_boundary"]
    assert "PRIVATE CUE CONTENT MUST NOT PROPAGATE" not in encoded
    assert "cue_label" not in encoded
    assert "viewer_count" not in encoded
    assert any("Safe Hold" in item for item in payload["guidance"])


def test_empty_history_returns_baseline_without_invented_metrics():
    payload = trends.build_trends([])
    assert payload["sessions"]["count"] == 0
    assert payload["sessions"]["completion_rate"] is None
    assert payload["latest"] is None
    assert payload["latest_vs_prior"] is None
    assert payload["recurring_segment_overruns"] == []
    assert "establish a cross-session operational baseline" in payload["guidance"][0]


def test_load_trends_queries_only_the_authenticated_tenant(tmp_path, monkeypatch):
    db = tmp_path / "trend-tenant.sqlite3"
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE live_show_runs(
            id TEXT PRIMARY KEY,user_id TEXT NOT NULL,status TEXT NOT NULL,
            ended_at TEXT,started_at TEXT
        )"""
    )
    con.executemany(
        "INSERT INTO live_show_runs(id,user_id,status,ended_at,started_at) VALUES(?,?,?,?,?)",
        [
            ("u1-run", "u1", "completed", "2026-09-01T02:00:00+00:00", "2026-09-01T01:00:00+00:00"),
            ("u2-run", "u2", "completed", "2026-09-01T03:00:00+00:00", "2026-09-01T02:00:00+00:00"),
        ],
    )
    con.commit()
    con.close()

    def connect():
        value = sqlite3.connect(db)
        value.row_factory = sqlite3.Row
        return value

    monkeypatch.setattr(trends.post_show, "_connect", connect)
    monkeypatch.setattr(trends.post_show, "build_report", lambda _con, row: _report(row["id"]))

    payload = trends._load_trends("u1", 12)
    assert payload["sessions"]["count"] == 1
    assert payload["latest"]["run_id"] == "u1-run"
    assert "u2-run" not in json.dumps(payload)


def test_api_and_page_require_member_and_return_private_headers(monkeypatch):
    with pytest.raises(HTTPException) as missing:
        trends.trend_coach_page(SimpleNamespace(state=SimpleNamespace(member=None)))
    assert missing.value.status_code == 401

    monkeypatch.setattr(trends, "_load_trends", lambda _user_id, _limit: trends.build_trends([]))
    api = trends.live_show_trends(_request(), limit=12)
    assert api.headers["cache-control"] == "private, no-store"
    assert api.headers["referrer-policy"] == "no-referrer"

    page = trends.trend_coach_page(_request())
    text = page.body.decode("utf-8")
    assert page.headers["cache-control"] == "private, no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert "Aura LIVE Cross-Session Trend Coach" in text
    assert "Operational patterns, not invented provider metrics" in text
    assert "/live-overlay-studio/post-show" in text
    assert "no provider write authority or TikTok LIVE control" in text

from __future__ import annotations

import pytest

from aura_music_studio.tier2_daily_meter import Tier2DailyMeter
from aura_music_studio.tier2_provider_guard import Tier2ProviderGuard


def test_tier2_provider_success_completes_reserved_operation(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    guard = Tier2ProviderGuard(meter)

    result, admission = guard.execute(
        user_id="member-1",
        plan_id="base",
        operation="music_create",
        request_key="music-job-1",
        provider_call=lambda: {"job_id": "provider-123"},
    )

    assert result == {"job_id": "provider-123"}
    assert admission.reservation_id is not None
    assert meter.usage("member-1", "base")["used"] == 1
    with meter._connect() as con:
        row = con.execute(
            "SELECT state FROM tier2_daily_operations WHERE id=?",
            (admission.reservation_id,),
        ).fetchone()
    assert row["state"] == "completed"


def test_provider_exception_releases_tier2_capacity_for_retry(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    guard = Tier2ProviderGuard(meter)

    def fail():
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        guard.execute(
            user_id="member-1",
            plan_id="base",
            operation="video_create",
            request_key="video-job-1",
            provider_call=fail,
        )

    assert meter.usage("member-1", "base")["used"] == 0

    result, retried = guard.execute(
        user_id="member-1",
        plan_id="base",
        operation="video_create",
        request_key="video-job-1",
        provider_call=lambda: "accepted",
    )
    assert result == "accepted"
    assert retried.state == "reserved"
    assert meter.usage("member-1", "base")["used"] == 1


def test_unlimited_pro_executes_without_persisting_daily_usage(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    guard = Tier2ProviderGuard(meter)

    result, admission = guard.execute(
        user_id="member-pro",
        plan_id="pro",
        operation="game_edit",
        request_key="game-job-1",
        provider_call=lambda: 42,
    )

    assert result == 42
    assert admission.unlimited is True
    assert admission.reservation_id is None
    assert meter.usage("member-pro", "pro")["unlimited"] is True


def test_free_membership_cannot_be_upgraded_through_provider_guard(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    guard = Tier2ProviderGuard(meter)
    called = False

    def provider():
        nonlocal called
        called = True
        return "must-not-run"

    with pytest.raises(PermissionError, match="separate entitlement path"):
        guard.execute(
            user_id="member-free",
            plan_id="free",
            operation="music_create",
            request_key="free-job-1",
            provider_call=provider,
        )

    assert called is False
    assert meter.usage("member-free", "free")["requires_separate_entitlement"] is True


def test_same_request_key_cannot_change_studio_operation(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    guard = Tier2ProviderGuard(meter)

    guard.execute(
        user_id="member-1",
        plan_id="base",
        operation="music_edit",
        request_key="stable-request",
        provider_call=lambda: "ok",
    )

    with pytest.raises(ValueError, match="already bound"):
        guard.execute(
            user_id="member-1",
            plan_id="base",
            operation="video_edit",
            request_key="stable-request",
            provider_call=lambda: "must-not-run",
        )

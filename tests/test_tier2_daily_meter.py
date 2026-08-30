from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aura_music_studio.tier2_daily_meter import TIER2_DAILY_LIMIT, Tier2DailyMeter


def _now() -> datetime:
    return datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def test_tier2_allows_five_cross_studio_operations_then_fails_closed(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    operations = [
        "music_create",
        "video_create",
        "game_create",
        "music_edit",
        "video_edit",
    ]

    for index, operation in enumerate(operations, start=1):
        admission = meter.reserve("member-1", "base", operation, f"request-{index}", now=_now())
        assert admission.state == "reserved"
        assert admission.used == index
        meter.complete("member-1", admission.reservation_id)

    usage = meter.usage("member-1", "base", now=_now())
    assert usage == {
        "plan": "base",
        "limit": TIER2_DAILY_LIMIT,
        "used": 5,
        "remaining": 0,
        "unlimited": False,
        "timezone": "UTC",
        "membership_effect": "none",
        "esp_role_effect": "none",
    }

    with pytest.raises(PermissionError, match="daily eligible-operation allowance"):
        meter.reserve("member-1", "base", "game_edit", "request-6", now=_now())


def test_same_request_key_is_idempotent_and_cannot_change_operation(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    first = meter.reserve("member-1", "base", "music_create", "stable-key", now=_now())
    second = meter.reserve("member-1", "base", "music_create", "stable-key", now=_now())

    assert second.reservation_id == first.reservation_id
    assert second.used == 1
    assert meter.usage("member-1", "base", now=_now())["used"] == 1

    with pytest.raises(ValueError, match="already bound"):
        meter.reserve("member-1", "base", "video_create", "stable-key", now=_now())


def test_release_returns_capacity_and_same_key_can_be_safely_readmitted(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    admission = meter.reserve("member-1", "base", "game_create", "retryable", now=_now())
    released = meter.release("member-1", admission.reservation_id)

    assert released["state"] == "released"
    assert meter.usage("member-1", "base", now=_now())["used"] == 0

    retried = meter.reserve("member-1", "base", "game_create", "retryable", now=_now())
    assert retried.reservation_id == admission.reservation_id
    assert retried.state == "reserved"
    assert retried.used == 1


def test_unlimited_pro_is_exempt_without_creating_a_daily_reservation(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    admission = meter.reserve("member-pro", "pro", "video_edit", "pro-request", now=_now())

    assert admission.unlimited is True
    assert admission.state == "unlimited"
    assert admission.reservation_id is None
    assert meter.usage("member-pro", "pro", now=_now())["unlimited"] is True


def test_free_plan_cannot_gain_tier2_entitlement_or_esp_role_from_meter(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    usage = meter.usage("member-free", "free", now=_now())

    assert usage["requires_separate_entitlement"] is True
    assert usage["membership_effect"] == "none"
    assert usage["esp_role_effect"] == "none"
    with pytest.raises(PermissionError, match="separate entitlement path"):
        meter.reserve("member-free", "free", "music_create", "free-request", now=_now())


def test_meter_uses_utc_day_boundary_and_validates_operation_and_key(tmp_path):
    meter = Tier2DailyMeter(tmp_path / "meter.sqlite3")
    first = meter.reserve("member-1", "base", "music_create", "day-one", now=_now())
    meter.complete("member-1", first.reservation_id)

    next_day = _now() + timedelta(days=1)
    assert meter.usage("member-1", "base", now=next_day)["used"] == 0

    with pytest.raises(ValueError, match="Unsupported"):
        meter.reserve("member-1", "base", "image_create", "bad-operation", now=_now())
    with pytest.raises(ValueError, match="idempotency"):
        meter.reserve("member-1", "base", "music_create", "", now=_now())
    with pytest.raises(ValueError, match="timezone-aware"):
        meter.usage("member-1", "base", now=datetime(2026, 8, 30, 12, 0))

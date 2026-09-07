from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aura_music_studio.aura_sec_health import evaluate_device_health, summarize_security_health


NOW = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)


def device(**overrides):
    data = {
        "id": "device_1234567890abcdef",
        "status": "enrolled",
        "protection_state": "healthy",
        "last_seen_at": NOW.isoformat(),
        "last_policy_version": "policy-1",
        "agent_version": "0.1.0",
        "revoked_at": None,
    }
    data.update(overrides)
    return data


def test_recent_verified_healthy_state_counts_as_healthy():
    result = evaluate_device_health(
        device(last_seen_at=(NOW - timedelta(seconds=30)).isoformat()),
        now=NOW,
    )
    assert result.health == "healthy"
    assert result.heartbeat_age_seconds == 30


def test_old_healthy_string_does_not_count_as_current_health():
    result = evaluate_device_health(
        device(last_seen_at=(NOW - timedelta(minutes=20)).isoformat()),
        now=NOW,
    )
    assert result.health == "stale"
    assert "too old" in result.reason


def test_enrolled_device_without_heartbeat_is_not_presented_as_protected():
    result = evaluate_device_health(device(last_seen_at=None, protection_state="awaiting_heartbeat"), now=NOW)
    assert result.health == "awaiting_verified_heartbeat"


def test_revoked_device_is_never_managed_even_if_old_state_says_healthy():
    result = evaluate_device_health(
        device(
            status="revoked",
            revoked_at=(NOW - timedelta(minutes=1)).isoformat(),
            last_seen_at=(NOW - timedelta(seconds=10)).isoformat(),
            protection_state="healthy",
        ),
        now=NOW,
    )
    assert result.health == "not_managed"
    assert result.protection_state == "not_managed"


def test_overview_requires_every_managed_device_to_be_fresh_and_healthy():
    result = summarize_security_health(
        [
            device(id="device_1111111111111111", last_seen_at=(NOW - timedelta(seconds=10)).isoformat()),
            device(id="device_2222222222222222", last_seen_at=(NOW - timedelta(minutes=15)).isoformat()),
        ],
        now=NOW,
    )
    assert result["overall"] == "attention_required"
    assert result["managed_devices"] == 2
    assert result["healthy_devices"] == 1
    assert result["attention_devices"] == 1

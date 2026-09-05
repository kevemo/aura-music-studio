from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aura_music_studio.creation_live_community import _needs_source_revalidation


def test_live_source_revalidation_is_bounded_to_thirty_seconds():
    now = datetime(2026, 9, 5, 2, 30, tzinfo=timezone.utc)
    fresh = {"updated_at": (now - timedelta(seconds=10)).isoformat()}
    due = {"updated_at": (now - timedelta(seconds=31)).isoformat()}

    assert _needs_source_revalidation(fresh, now=now) is False
    assert _needs_source_revalidation(due, now=now) is True


def test_missing_or_malformed_source_timestamp_fails_toward_revalidation():
    now = datetime(2026, 9, 5, 2, 30, tzinfo=timezone.utc)
    assert _needs_source_revalidation({}, now=now) is True
    assert _needs_source_revalidation({"updated_at": "not-a-date"}, now=now) is True

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_scheduler import SharedSkyScheduler, router
from aura_music_studio.shared_sky_security import SharedSkyVault
from aura_music_studio.shared_sky_streaming_studios import (
    DestinationCreate,
    ProjectCreate,
    ScheduleCreate,
    SharedSkyStore,
    SourceCreate,
)


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup("scheduler@example.com", "Scheduler Creator", "a-very-secure-test-password", "free")
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    store = SharedSkyStore(EspStore(accounts), SharedSkyVault("unit-test-shared-sky-secret"))
    project = store.create_project(user["id"], ProjectCreate(name="Scheduled Show"))
    store.create_source(
        user["id"],
        project["scenes"][0]["id"],
        SourceCreate(source_type="camera", name="Host Camera"),
    )
    destination = store.create_destination(
        user["id"],
        DestinationCreate(
            platform_id="custom-rtmp",
            label="Scheduled destination",
            auth_mode="custom_rtmp",
            endpoint="rtmps://example.invalid/live",
            credential="test-key",
        ),
    )
    return user, store, project, destination


def test_due_live_schedule_is_claimed_once_and_creates_one_broadcast(tmp_path, monkeypatch):
    user, store, project, destination = _setup(tmp_path)
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    schedule = store.create_schedule(
        user["id"],
        ScheduleCreate(
            project_id=project["id"],
            title="Friday Scheduled LIVE",
            start_at=due.isoformat(),
            destination_ids=[destination["id"]],
            mode="live",
        ),
    )
    scheduler = SharedSkyScheduler(store, enabled=True)
    started = []

    def fake_start(user_id: str, broadcast_id: str):
        started.append((user_id, broadcast_id))
        return {"started_outputs": 1}

    monkeypatch.setattr(store, "start_broadcast", fake_start)

    result = scheduler.run_due(now=datetime.now(timezone.utc))
    assert result["claimed"] == 1
    assert result["started"] == 1
    assert len(started) == 1

    updated = store.schedule(user["id"], schedule["id"])
    assert updated["state"] == "started"
    assert updated["broadcast_id"]
    assert updated["last_error"] == ""
    assert len(store.broadcasts(user["id"])) == 1

    repeated = scheduler.run_due(now=datetime.now(timezone.utc) + timedelta(minutes=1))
    assert repeated["claimed"] == 0
    assert len(started) == 1
    assert len(store.broadcasts(user["id"])) == 1


def test_future_schedule_is_not_claimed_early(tmp_path):
    user, store, project, destination = _setup(tmp_path)
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    schedule = store.create_schedule(
        user["id"],
        ScheduleCreate(
            project_id=project["id"],
            title="Later LIVE",
            start_at=future.isoformat(),
            destination_ids=[destination["id"]],
            mode="live",
        ),
    )
    scheduler = SharedSkyScheduler(store, enabled=True)
    result = scheduler.run_due(now=datetime.now(timezone.utc))
    assert result["claimed"] == 0
    assert store.schedule(user["id"], schedule["id"])["state"] == "scheduled"


def test_pre_recorded_schedule_fails_closed_until_playout_worker_exists(tmp_path):
    user, store, project, destination = _setup(tmp_path)
    schedule = store.create_schedule(
        user["id"],
        ScheduleCreate(
            project_id=project["id"],
            title="Recorded Premiere",
            start_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
            destination_ids=[destination["id"]],
            mode="pre_recorded",
        ),
    )
    scheduler = SharedSkyScheduler(store, enabled=True)
    result = scheduler.run_due(now=datetime.now(timezone.utc))
    assert result["claimed"] == 1
    assert result["started"] == 0
    assert len(result["failed"]) == 1
    updated = store.schedule(user["id"], schedule["id"])
    assert updated["state"] == "failed"
    assert "media playout worker" in updated["last_error"]
    assert updated["broadcast_id"] is None


def test_invalid_schedule_timestamp_is_marked_failed_not_started(tmp_path):
    user, store, project, destination = _setup(tmp_path)
    schedule = store.create_schedule(
        user["id"],
        ScheduleCreate(
            project_id=project["id"],
            title="Bad timestamp",
            start_at="not-a-real-time",
            destination_ids=[destination["id"]],
            mode="live",
        ),
    )
    scheduler = SharedSkyScheduler(store, enabled=True)
    result = scheduler.run_due(now=datetime.now(timezone.utc))
    assert result["started"] == 0
    updated = store.schedule(user["id"], schedule["id"])
    assert updated["state"] == "failed"
    assert "ISO-8601" in updated["last_error"]
    assert store.broadcasts(user["id"]) == []


def test_scheduler_owner_routes_are_mounted_by_scheduler_router():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/owner/shared-sky/api/scheduler/status" in paths
    assert "/owner/shared-sky/api/scheduler/run-due" in paths

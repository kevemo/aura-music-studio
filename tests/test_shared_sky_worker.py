from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.shared_sky_security import SharedSkyVault
from aura_music_studio.shared_sky_streaming_studios import (
    DestinationCreate,
    ProjectCreate,
    ScheduleCreate,
    SharedSkyStore,
    SourceCreate,
)
from aura_music_studio.shared_sky_worker import SharedSkyWorker, WorkerSettings


def _setup(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    signup = accounts.signup(
        "scheduled@example.com",
        "Scheduled Creator",
        "a-very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Owner")
    esp = EspStore(accounts)
    store = SharedSkyStore(esp, SharedSkyVault("test-shared-sky-worker-secret"))
    settings = WorkerSettings(
        enabled=True,
        poll_seconds=1,
        lease_seconds=60,
        max_attempts=2,
        retry_seconds=15,
    )
    return user, store, settings


def _live_schedule(store, user_id: str, when: datetime):
    project = store.create_project(user_id, ProjectCreate(name="Scheduled Show"))
    scene_id = project["scenes"][0]["id"]
    store.create_source(user_id, scene_id, SourceCreate(source_type="camera", name="Camera"))
    destination = store.create_destination(
        user_id,
        DestinationCreate(
            platform_id="custom-rtmp",
            label="Test destination",
            auth_mode="custom_rtmp",
            endpoint="rtmps://example.invalid/live",
            credential="test-key",
        ),
    )
    schedule = store.create_schedule(
        user_id,
        ScheduleCreate(
            project_id=project["id"],
            title="Scheduled LIVE",
            start_at=when.isoformat(),
            destination_ids=[destination["id"]],
            mode="live",
        ),
    )
    return project, destination, schedule


def test_due_schedule_is_claimed_only_once_across_workers(tmp_path):
    user, store, settings = _setup(tmp_path)
    now = datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc)
    _, _, schedule = _live_schedule(store, user["id"], now - timedelta(seconds=1))

    first = SharedSkyWorker(store, settings=settings, worker_id="worker-a")
    second = SharedSkyWorker(store, settings=settings, worker_id="worker-b")

    claimed = first.claim_due_schedule(now=now)
    assert claimed is not None
    assert claimed["id"] == schedule["id"]
    assert claimed["claimed_by"] == "worker-a"
    assert claimed["state"] == "starting"
    assert claimed["attempt_count"] == 1

    assert second.claim_due_schedule(now=now) is None


def test_future_schedule_is_not_claimed(tmp_path):
    user, store, settings = _setup(tmp_path)
    now = datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc)
    _live_schedule(store, user["id"], now + timedelta(hours=2))
    worker = SharedSkyWorker(store, settings=settings, worker_id="worker-future")
    assert worker.claim_due_schedule(now=now) is None


def test_schedule_reuses_one_broadcast_across_retry(tmp_path, monkeypatch):
    user, store, settings = _setup(tmp_path)
    now = datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc)
    _, _, schedule = _live_schedule(store, user["id"], now - timedelta(seconds=1))
    worker = SharedSkyWorker(store, settings=settings, worker_id="worker-retry")

    first_claim = worker.claim_due_schedule(now=now)
    result = worker.execute_claimed(first_claim, now=now)
    assert result["ok"] is False
    assert result["broadcast_id"]

    with worker._connect() as con:
        row = con.execute(
            "SELECT state,broadcast_id,next_attempt_at FROM shared_sky_schedules WHERE id=?",
            (schedule["id"],),
        ).fetchone()
    assert row["state"] == "retry"
    first_broadcast_id = row["broadcast_id"]
    assert first_broadcast_id == result["broadcast_id"]

    retry_time = now + timedelta(seconds=settings.retry_seconds + 1)
    second_claim = worker.claim_due_schedule(now=retry_time)
    assert second_claim is not None
    assert second_claim["broadcast_id"] == first_broadcast_id

    def fake_start(user_id: str, broadcast_id: str):
        assert user_id == user["id"]
        assert broadcast_id == first_broadcast_id
        return {"broadcast": {"id": broadcast_id, "state": "live"}}

    monkeypatch.setattr(store, "start_broadcast", fake_start)
    second = worker.execute_claimed(second_claim, now=retry_time)
    assert second["ok"] is True
    assert second["broadcast_id"] == first_broadcast_id

    with worker._connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM shared_sky_broadcasts WHERE user_id=?",
            (user["id"],),
        ).fetchone()[0]
        state = con.execute(
            "SELECT state FROM shared_sky_schedules WHERE id=?", (schedule["id"],)
        ).fetchone()[0]
    assert count == 1
    assert state == "live"


def test_pre_recorded_schedule_fails_closed_without_playout_worker(tmp_path):
    user, store, _ = _setup(tmp_path)
    now = datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc)
    project = store.create_project(user["id"], ProjectCreate(name="Premiere"))
    schedule = store.create_schedule(
        user["id"],
        ScheduleCreate(
            project_id=project["id"],
            title="Premiere",
            start_at=(now - timedelta(seconds=1)).isoformat(),
            destination_ids=[],
            mode="pre_recorded",
        ),
    )
    settings = WorkerSettings(
        enabled=True,
        poll_seconds=1,
        lease_seconds=60,
        max_attempts=1,
        retry_seconds=15,
    )
    worker = SharedSkyWorker(store, settings=settings, worker_id="worker-playout")
    claim = worker.claim_due_schedule(now=now)
    result = worker.execute_claimed(claim, now=now)
    assert result["ok"] is False
    assert "playout" in result["error"].lower()

    with worker._connect() as con:
        row = con.execute(
            "SELECT state,last_error FROM shared_sky_schedules WHERE id=?", (schedule["id"],)
        ).fetchone()
    assert row["state"] == "failed"
    assert "playout" in row["last_error"].lower()


def test_worker_heartbeat_exposes_fresh_health(tmp_path):
    _, store, settings = _setup(tmp_path)
    worker = SharedSkyWorker(store, settings=settings, worker_id="worker-health")
    worker.heartbeat(status="idle")
    health = worker.worker_health(stale_after_seconds=180)
    row = next(item for item in health if item["worker_id"] == "worker-health")
    assert row["status"] == "idle"
    assert row["healthy"] is True

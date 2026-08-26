from datetime import datetime, timezone
from pathlib import Path

import pytest

from aura_music_studio.esp_social_publish_queue import SocialPublishQueue
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import PlatformVariant, SocialConnection, SocialContent, SocialHouseStore


def with_user(tmp_path: Path, monkeypatch, user_id: str):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    return set_current_user_id(user_id)


def active_connection() -> SocialConnection:
    return SocialConnection(
        platform="instagram",
        account_label="Creator IG",
        state="connected",
        supports_auto_publish=True,
        token_secret_ref="social-token://instagram_creator",
        metadata={
            "publishing_adapter": "instagram_graph",
            "publishing_adapter_active": True,
        },
    )


def scheduled_content(*, scheduled_at: str, status: str = "approved", approval_required: bool = False) -> SocialContent:
    return SocialContent(
        title="Launch Reel",
        status=status,
        approval_required=approval_required,
        variants=[
            PlatformVariant(
                platform="instagram",
                content_type="reel",
                caption="Launch day",
                scheduled_at=scheduled_at,
                timezone="Europe/London",
                auto_publish=True,
            )
        ],
    )


def test_queue_blocks_when_official_adapter_is_not_active(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "blocked-adapter")
    try:
        store = SocialHouseStore()
        house = store.create_space("Blocked Adapter")
        store.connect_placeholder(
            house.id,
            SocialConnection(
                platform="instagram",
                account_label="Creator IG",
                state="connected",
                supports_auto_publish=True,
            ),
        )
        content = scheduled_content(scheduled_at="2026-08-26T01:00:00+01:00")
        store.add_content(house.id, content)

        snapshot = SocialPublishQueue(store).refresh(
            house.id,
            now=datetime(2026, 8, 26, 2, 0, tzinfo=timezone.utc),
        )
        entry = snapshot.entries[0]
        assert entry.state == "blocked"
        assert "restricted social-token:// alias format" in entry.reasons[0]
        assert "official publishing adapter not active" in entry.reasons
        assert store.load(house.id).content[0].variants[0].publish_state == "blocked"
    finally:
        reset_current_user_id(token)


def test_future_and_due_variants_move_from_planned_to_queued(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "schedule")
    try:
        store = SocialHouseStore()
        house = store.create_space("Schedule")
        store.connect_placeholder(house.id, active_connection())
        content = scheduled_content(scheduled_at="2026-08-26T18:00:00+01:00")
        store.add_content(house.id, content)
        queue = SocialPublishQueue(store)

        future = queue.refresh(house.id, now=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc))
        assert future.entries[0].state == "planned"
        assert future.entries[0].due is False

        due = queue.refresh(house.id, now=datetime(2026, 8, 26, 18, 0, tzinfo=timezone.utc))
        assert due.entries[0].state == "queued"
        assert due.entries[0].due is True
        assert store.load(house.id).content[0].variants[0].publish_state == "queued"
    finally:
        reset_current_user_id(token)


def test_approval_gate_remains_authoritative(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "approval-queue")
    try:
        store = SocialHouseStore()
        house = store.create_space("Approval Queue")
        store.connect_placeholder(house.id, active_connection())
        content = scheduled_content(
            scheduled_at="2026-08-26T01:00:00+01:00",
            status="pending_approval",
            approval_required=True,
        )
        store.add_content(house.id, content)
        queue = SocialPublishQueue(store)

        blocked = queue.refresh(house.id, now=datetime(2026, 8, 26, 2, 0, tzinfo=timezone.utc))
        assert blocked.entries[0].state == "blocked"
        assert "approval gate not satisfied" in blocked.entries[0].reasons

        store.approve_content(house.id, content.id, "mentor")
        retried = queue.retry(
            house.id,
            blocked.entries[0].id,
            actor="creator",
            now=datetime(2026, 8, 26, 2, 0, tzinfo=timezone.utc),
        )
        assert retried.state == "queued"
        assert retried.retry_requests == 1
    finally:
        reset_current_user_id(token)


def test_naive_schedule_uses_explicit_iana_timezone(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "timezone")
    try:
        store = SocialHouseStore()
        house = store.create_space("Timezone")
        store.connect_placeholder(house.id, active_connection())
        content = scheduled_content(scheduled_at="2026-08-26T18:00:00")
        store.add_content(house.id, content)

        entry = SocialPublishQueue(store).snapshot(
            house.id,
            now=datetime(2026, 8, 26, 16, 30, tzinfo=timezone.utc),
        ).entries[0]
        assert entry.scheduled_at_utc == "2026-08-26T17:00:00+00:00"
        assert entry.due is False
    finally:
        reset_current_user_id(token)


def test_published_state_requires_provider_confirmation(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "provider-confirmation")
    try:
        store = SocialHouseStore()
        house = store.create_space("Provider Confirmation")
        store.connect_placeholder(house.id, active_connection())
        content = scheduled_content(scheduled_at="2026-08-26T01:00:00+01:00")
        store.add_content(house.id, content)
        queue = SocialPublishQueue(store)
        queued = queue.refresh(house.id, now=datetime(2026, 8, 26, 2, 0, tzinfo=timezone.utc))
        assert queued.entries[0].state == "queued"
        assert store.load(house.id).content[0].status != "published"

        with pytest.raises(ValueError, match="external_post_id"):
            queue.confirm_published(
                house.id,
                queued.entries[0].id,
                adapter_name="instagram_graph",
                external_post_id="",
            )

        confirmed = queue.confirm_published(
            house.id,
            queued.entries[0].id,
            adapter_name="instagram_graph",
            external_post_id="ig_12345",
            external_post_url="https://example.invalid/provider-post/ig_12345",
        )
        assert confirmed.state == "published"
        assert confirmed.external_post_id == "ig_12345"
        persisted = store.load(house.id)
        assert persisted.content[0].status == "published"
        assert persisted.content[0].variants[0].metadata["published_via_adapter"] == "instagram_graph"
    finally:
        reset_current_user_id(token)


def test_wrong_adapter_cannot_fake_provider_confirmation(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "wrong-adapter")
    try:
        store = SocialHouseStore()
        house = store.create_space("Wrong Adapter")
        store.connect_placeholder(house.id, active_connection())
        content = scheduled_content(scheduled_at="2026-08-26T01:00:00+01:00")
        store.add_content(house.id, content)
        queue = SocialPublishQueue(store)
        entry = queue.refresh(house.id, now=datetime(2026, 8, 26, 2, 0, tzinfo=timezone.utc)).entries[0]

        with pytest.raises(ValueError, match="active authorised"):
            queue.confirm_published(
                house.id,
                entry.id,
                adapter_name="fake_adapter",
                external_post_id="fake_1",
            )
        assert store.load(house.id).content[0].variants[0].publish_state == "queued"
    finally:
        reset_current_user_id(token)


def test_worker_claim_provider_job_failure_and_retry_are_audited(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "worker-lifecycle")
    try:
        store = SocialHouseStore()
        house = store.create_space("Worker Lifecycle")
        store.connect_placeholder(house.id, active_connection())
        content = scheduled_content(scheduled_at="2026-08-26T01:00:00+01:00")
        store.add_content(house.id, content)
        queue = SocialPublishQueue(store)
        entry = queue.refresh(house.id, now=datetime(2026, 8, 26, 2, 0, tzinfo=timezone.utc)).entries[0]

        claimed = queue.claim(
            house.id,
            entry.id,
            worker_id="worker-test",
            now=datetime(2026, 8, 26, 2, 1, tzinfo=timezone.utc),
        )
        assert claimed.state == "publishing"
        assert claimed.worker_id == "worker-test"

        pending = queue.record_provider_pending(
            house.id,
            entry.id,
            adapter_name="instagram_graph",
            provider_job_id="container_123",
            worker_id="worker-test",
            provider_metadata={"stage": "container"},
            now=datetime(2026, 8, 26, 2, 2, tzinfo=timezone.utc),
        )
        assert pending.state == "publishing"
        assert pending.provider_job_id == "container_123"

        failed = queue.fail_provider_job(
            house.id,
            entry.id,
            adapter_name="instagram_graph",
            reason="provider test failure",
            worker_id="worker-test",
            retryable=True,
            now=datetime(2026, 8, 26, 2, 3, tzinfo=timezone.utc),
        )
        assert failed.state == "failed"
        assert "provider test failure" in failed.reasons

        retried = queue.retry(
            house.id,
            entry.id,
            actor="creator",
            now=datetime(2026, 8, 26, 2, 4, tzinfo=timezone.utc),
        )
        assert retried.state == "queued"
        assert retried.provider_job_id is None
        assert retried.retry_requests == 1
        actions = [event.action for event in store.load(house.id).activity]
        assert "provider_publish_claimed" in actions
        assert "provider_publish_failed" in actions
        assert "publish_retry_requested" in actions
    finally:
        reset_current_user_id(token)

from __future__ import annotations

import pytest

from aura_music_studio import esp_product_workflows_scheduling_hardening as _scheduling_hardening  # noqa: F401
from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_command_center import EspStore
from aura_music_studio.esp_product_workflows import AnnouncementCreate, Chat9WorkflowStore


def _store(tmp_path) -> Chat9WorkflowStore:
    accounts = AccountStore(tmp_path / "chat9-scheduling.sqlite3")
    return Chat9WorkflowStore(EspStore(accounts))


def test_scheduled_announcement_requires_explicit_future_publish_at(tmp_path):
    workflows = _store(tmp_path)

    with pytest.raises(ValueError, match="publish_at is required for scheduled announcements"):
        workflows.create_announcement(
            AnnouncementCreate(
                title="Scheduled update",
                body="This must not publish immediately.",
                audience="everyone",
                status="scheduled",
                confirm_publish=True,
            ),
            actor_user_id="owner-test",
        )

    with pytest.raises(ValueError, match="publish_at must include a timezone offset"):
        workflows.create_announcement(
            AnnouncementCreate(
                title="Scheduled update",
                body="Timezone is required.",
                audience="everyone",
                status="scheduled",
                publish_at="2099-01-01T12:00:00",
                confirm_publish=True,
            ),
            actor_user_id="owner-test",
        )


def test_scheduled_announcement_is_normalised_and_hidden_until_due(tmp_path):
    workflows = _store(tmp_path)
    announcement = workflows.create_announcement(
        AnnouncementCreate(
            title="Future update",
            body="Visible only when the scheduled UTC instant is reached.",
            audience="everyone",
            status="scheduled",
            publish_at="2099-01-01T12:00:00Z",
            expires_at="2099-01-02T12:00:00+00:00",
            confirm_publish=True,
            reason="Regression coverage for scheduling semantics",
        ),
        actor_user_id="owner-test",
    )

    assert announcement["publish_at"] == "2099-01-01T12:00:00+00:00"
    assert announcement["expires_at"] == "2099-01-02T12:00:00+00:00"
    assert workflows.visible_announcements("member-test", {"status": "active", "roles": "creator"}) == []


def test_published_announcement_cannot_smuggle_a_future_publish_at(tmp_path):
    workflows = _store(tmp_path)

    with pytest.raises(ValueError, match="use scheduled"):
        workflows.create_announcement(
            AnnouncementCreate(
                title="Future published row",
                body="Published status and future scheduling cannot be mixed.",
                audience="everyone",
                status="published",
                publish_at="2099-01-01T12:00:00+00:00",
                confirm_publish=True,
            ),
            actor_user_id="owner-test",
        )

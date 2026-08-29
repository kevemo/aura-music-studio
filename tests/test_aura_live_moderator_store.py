from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from aura_music_studio.aura_live_moderator import AuraModeratorAuthorization, ModerationMode
from aura_music_studio.aura_live_moderator_store import AuraLiveModeratorStore


def _store(tmp_path) -> AuraLiveModeratorStore:
    return AuraLiveModeratorStore(tmp_path / "aura-live-moderator.sqlite3")


def _authorized(*, mode: ModerationMode = ModerationMode.ASSISTED) -> AuraModeratorAuthorization:
    return AuraModeratorAuthorization(
        creator_handle="creator.one",
        creator_consent=True,
        moderator_assignment_confirmed=True,
        mode=mode,
    )


def test_creator_can_save_authorization_but_cannot_self_enable_provider_write(tmp_path):
    store = _store(tmp_path)
    requested = _authorized().model_copy(update={"provider_write_enabled": True})

    saved = store.save_creator_authorization(
        user_id="member-1", authorization=requested, actor="member:member-1"
    )

    assert saved.authorization.creator_handle == "creator.one"
    assert saved.authorization.provider_write_enabled is False
    assert saved.provider_approval_ref is None
    assert store.verify_audit_chain("member-1") is True


def test_provider_write_enablement_requires_review_reference_and_creator_boundaries(tmp_path):
    store = _store(tmp_path)
    store.save_creator_authorization(
        user_id="member-1", authorization=_authorized(), actor="member:member-1"
    )

    with pytest.raises(ValueError, match="approval_ref"):
        store.set_provider_write_enabled(
            user_id="member-1", enabled=True, actor="owner:reviewer", approval_ref=""
        )

    enabled = store.set_provider_write_enabled(
        user_id="member-1",
        enabled=True,
        actor="owner:reviewer",
        approval_ref="tiktok-partner-approval-2026-001",
    )

    assert enabled.authorization.provider_write_enabled is True
    assert enabled.provider_approval_ref == "tiktok-partner-approval-2026-001"
    assert store.verify_audit_chain("member-1") is True


def test_advisory_mode_cannot_enable_provider_write(tmp_path):
    store = _store(tmp_path)
    store.save_creator_authorization(
        user_id="member-1",
        authorization=_authorized(mode=ModerationMode.ADVISORY),
        actor="member:member-1",
    )

    with pytest.raises(ValueError, match="advisory"):
        store.set_provider_write_enabled(
            user_id="member-1",
            enabled=True,
            actor="owner:reviewer",
            approval_ref="approved-connector",
        )


def test_creator_policy_change_fails_closed_and_removes_prior_provider_write_state(tmp_path):
    store = _store(tmp_path)
    store.save_creator_authorization(
        user_id="member-1", authorization=_authorized(), actor="member:member-1"
    )
    store.set_provider_write_enabled(
        user_id="member-1",
        enabled=True,
        actor="owner:reviewer",
        approval_ref="approved-connector",
    )

    changed = AuraModeratorAuthorization(
        creator_handle="creator.one",
        creator_consent=True,
        moderator_assignment_confirmed=True,
        mode=ModerationMode.ADVISORY,
    )
    saved = store.save_creator_authorization(
        user_id="member-1", authorization=changed, actor="member:member-1"
    )

    assert saved.authorization.provider_write_enabled is False
    assert saved.provider_approval_ref is None


def test_audit_metadata_rejects_tiktok_or_session_secrets(tmp_path):
    store = _store(tmp_path)

    for forbidden_key in (
        "tiktok_password",
        "session_cookie",
        "access_token",
        "provider_secret",
        "private_key",
        "api_credential",
    ):
        with pytest.raises(ValueError, match="secrets"):
            store.record_moderation_event(
                user_id="member-1",
                event_type="moderation_decision",
                actor="aura-live-moderator",
                metadata={forbidden_key: "must-never-persist"},
            )


def test_audit_chain_detects_database_tampering(tmp_path):
    database = tmp_path / "aura-live-moderator.sqlite3"
    store = AuraLiveModeratorStore(database)
    store.save_creator_authorization(
        user_id="member-1", authorization=_authorized(), actor="member:member-1"
    )
    store.record_moderation_event(
        user_id="member-1",
        event_type="moderation_decision",
        actor="aura-live-moderator",
        metadata={"category": "spam", "action": "recommend_mute"},
    )

    assert store.verify_audit_chain("member-1") is True

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE aura_live_moderation_audit SET metadata_json = ? WHERE user_id = ? AND event_type = ?",
            ('{"action":"allow","category":"spam"}', "member-1", "moderation_decision"),
        )

    assert store.verify_audit_chain("member-1") is False


def test_revoke_removes_current_authorization_but_preserves_audit_evidence(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 8, 29, 19, 40, tzinfo=UTC)
    store.save_creator_authorization(
        user_id="member-1",
        authorization=_authorized(),
        actor="member:member-1",
        now=now,
    )

    store.revoke(user_id="member-1", actor="member:member-1", now=now)

    assert store.get("member-1") is None
    events = store.recent_events("member-1")
    assert events[0].event_type == "authorization_revoked"
    assert events[-1].event_type == "authorization_saved"
    assert store.verify_audit_chain("member-1") is True

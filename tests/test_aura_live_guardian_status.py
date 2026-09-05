from aura_music_studio.aura_live_guardian_status import build_live_session_status
from aura_music_studio.aura_live_moderator import AuraModeratorAuthorization, ModerationMode
from aura_music_studio.aura_live_moderator_store import AuraLiveModeratorStore


def test_unconfigured_creator_needs_attention(tmp_path):
    status = build_live_session_status(database=tmp_path / "guardian.sqlite3", user_id="creator-1")
    assert status.safety_state == "attention"
    assert status.pre_live_ready is False
    assert status.provider_execution_ready is False


def test_ready_creator_still_has_no_inferred_provider_execution(tmp_path):
    database = tmp_path / "guardian.sqlite3"
    AuraLiveModeratorStore(database).save_creator_authorization(
        user_id="creator-1",
        authorization=AuraModeratorAuthorization(
            creator_handle="creator.one",
            creator_consent=True,
            moderator_assignment_confirmed=True,
            mode=ModerationMode.ASSISTED,
            provider_write_enabled=False,
        ),
        actor="member:creator-1",
    )
    status = build_live_session_status(database=database, user_id="creator-1")
    assert status.safety_state == "ready"
    assert status.pre_live_ready is True
    assert status.provider_execution_ready is False
    assert status.audit_integrity_ok is True
    assert status.mode == ModerationMode.ASSISTED.value


def test_status_is_bounded_and_contains_no_live_message_content(tmp_path):
    status = build_live_session_status(database=tmp_path / "guardian.sqlite3", user_id="creator-1")
    assert not hasattr(status, "message_text")
    assert not hasattr(status, "comment_text")
    assert not hasattr(status, "evidence")
    assert not hasattr(status, "access_token")

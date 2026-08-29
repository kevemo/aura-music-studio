from aura_music_studio.aura_live_guardian_readiness import assess_live_readiness
from aura_music_studio.aura_live_guardian_review import AuraLiveGuardianReviewStore
from aura_music_studio.aura_live_moderator import (
    AuraModeratorAuthorization,
    ModerationAction,
    ModerationDecision,
    ModerationMode,
)
from aura_music_studio.aura_live_moderator_store import AuraLiveModeratorStore


def _authorize(database, *, user_id="creator-1", consent=True, assignment=True, provider=True):
    store = AuraLiveModeratorStore(database)
    store.save_creator_authorization(
        user_id=user_id,
        actor=f"member:{user_id}",
        authorization=AuraModeratorAuthorization(
            creator_handle="creator.live",
            creator_consent=consent,
            moderator_assignment_confirmed=assignment,
            mode=ModerationMode.ASSISTED,
            provider_write_enabled=provider,
        ),
    )


def test_readiness_requires_authorization_consent_and_assignment(tmp_path):
    database = tmp_path / "guardian.sqlite3"
    report = assess_live_readiness(database=database, user_id="creator-1")
    assert report.pre_live_ready is False
    assert report.provider_execution_ready is False
    failed = {check.key for check in report.blocking_failures}
    assert {"authorization", "creator_consent", "moderator_assignment"}.issubset(failed)


def test_ready_persisted_guardian_state_still_never_asserts_provider_runtime(tmp_path):
    database = tmp_path / "guardian.sqlite3"
    _authorize(database)
    report = assess_live_readiness(database=database, user_id="creator-1")
    assert report.pre_live_ready is True
    assert report.mode == "assisted"
    assert report.pending_reviews == 0
    assert report.critical_escalations == 0
    # save_creator_authorization never allows a creator to self-enable provider writes, and even
    # reviewed historical approval would not prove a connector is currently capable at runtime.
    assert report.provider_execution_ready is False
    runtime = next(check for check in report.checks if check.key == "provider_runtime")
    assert runtime.passed is False
    assert runtime.blocking is False
    assert "fail-closed" in runtime.detail


def test_critical_escalation_blocks_pre_live_readiness_until_acknowledged(tmp_path):
    database = tmp_path / "guardian.sqlite3"
    _authorize(database)
    reviews = AuraLiveGuardianReviewStore(database)
    item = reviews.enqueue(
        user_id="creator-1",
        audit_event_id="audit-critical-1",
        signal_category="doxxing",
        signal_severity=4,
        confidence_bucket="very_high",
        decision=ModerationDecision(
            action=ModerationAction.ESCALATE,
            provider_write_permitted=False,
            requires_human_confirmation=True,
            reason="critical safety escalation",
        ),
    )
    assert item is not None
    blocked = assess_live_readiness(database=database, user_id="creator-1")
    assert blocked.pre_live_ready is False
    assert blocked.critical_escalations == 1
    assert "critical_reviews" in {check.key for check in blocked.blocking_failures}

    reviews.acknowledge_escalation(
        user_id="creator-1",
        review_id=item.review_id,
        actor="member:creator-1",
    )
    clear = assess_live_readiness(database=database, user_id="creator-1")
    assert clear.pre_live_ready is True
    assert clear.critical_escalations == 0


def test_noncritical_pending_review_is_visible_but_not_a_pre_live_blocker(tmp_path):
    database = tmp_path / "guardian.sqlite3"
    _authorize(database)
    reviews = AuraLiveGuardianReviewStore(database)
    item = reviews.enqueue(
        user_id="creator-1",
        audit_event_id="audit-review-1",
        signal_category="spam",
        signal_severity=2,
        confidence_bucket="high",
        decision=ModerationDecision(
            action=ModerationAction.RECOMMEND_MUTE,
            provider_write_permitted=True,
            requires_human_confirmation=True,
            reason="assisted moderation recommendation",
        ),
    )
    assert item is not None
    report = assess_live_readiness(database=database, user_id="creator-1")
    assert report.pre_live_ready is True
    assert report.pending_reviews == 1
    human_review = next(check for check in report.checks if check.key == "human_review")
    assert human_review.blocking is False
    assert human_review.passed is False


def test_default_policy_keeps_mandatory_protections_ready(tmp_path):
    database = tmp_path / "guardian.sqlite3"
    _authorize(database)
    report = assess_live_readiness(database=database, user_id="creator-1")
    mandatory = next(check for check in report.checks if check.key == "mandatory_safety")
    assert mandatory.passed is True
    assert "Default Guardian policy" in mandatory.detail

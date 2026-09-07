import pytest

from aura_music_studio.aura_live_moderator import (
    AURA_LIVE_MODERATOR_HANDLE,
    AURA_LIVE_MODERATOR_PROFILE_URL,
    AuraLiveModerator,
    AuraModeratorAuthorization,
    ModerationAction,
    ModerationMode,
    ModerationSignal,
    TikTokLiveConnectorCapabilities,
)


def test_official_aura_tiktok_moderator_identity_is_fixed():
    authorization = AuraModeratorAuthorization(creator_handle="@creator.one")
    assert authorization.aura_handle == AURA_LIVE_MODERATOR_HANDLE == "aura.chat.mod"
    assert authorization.profile_url == AURA_LIVE_MODERATOR_PROFILE_URL
    assert authorization.creator_handle == "creator.one"


def test_provider_writes_require_explicit_consent_assignment_and_non_advisory_mode():
    with pytest.raises(ValueError, match="explicit creator consent"):
        AuraModeratorAuthorization(
            creator_handle="creator.one",
            provider_write_enabled=True,
            moderator_assignment_confirmed=True,
            mode=ModerationMode.ASSISTED,
        )

    with pytest.raises(ValueError, match="confirmed TikTok moderator assignment"):
        AuraModeratorAuthorization(
            creator_handle="creator.one",
            creator_consent=True,
            provider_write_enabled=True,
            mode=ModerationMode.ASSISTED,
        )

    with pytest.raises(ValueError, match="Advisory mode"):
        AuraModeratorAuthorization(
            creator_handle="creator.one",
            creator_consent=True,
            moderator_assignment_confirmed=True,
            provider_write_enabled=True,
            mode=ModerationMode.ADVISORY,
        )


def test_creator_cannot_authorize_aura_profile_as_the_creator_identity():
    with pytest.raises(ValueError, match="cannot be the Aura moderator"):
        AuraModeratorAuthorization(creator_handle="@aura.chat.mod")


def test_no_consent_means_observe_only():
    moderator = AuraLiveModerator()
    authorization = AuraModeratorAuthorization(creator_handle="creator.one")
    decision = moderator.decide(
        authorization,
        ModerationSignal(category="harassment", confidence=0.99, severity=4),
        TikTokLiveConnectorCapabilities(
            approved_transport=True,
            can_read_live_comments=True,
            can_block=True,
        ),
    )
    assert decision.action is ModerationAction.OBSERVE
    assert decision.provider_write_permitted is False
    assert decision.requires_human_confirmation is True


def test_advisory_mode_never_allows_provider_write():
    moderator = AuraLiveModerator()
    authorization = AuraModeratorAuthorization(
        creator_handle="creator.one",
        creator_consent=True,
        moderator_assignment_confirmed=True,
        mode=ModerationMode.ADVISORY,
    )
    decision = moderator.decide(
        authorization,
        ModerationSignal(category="spam", confidence=0.95, severity=2),
        TikTokLiveConnectorCapabilities(
            approved_transport=True,
            can_mute=True,
        ),
    )
    assert decision.action is ModerationAction.RECOMMEND_MUTE
    assert decision.provider_write_permitted is False
    assert decision.requires_human_confirmation is True


def test_profile_assignment_does_not_create_api_write_authority():
    moderator = AuraLiveModerator()
    authorization = AuraModeratorAuthorization(
        creator_handle="creator.one",
        creator_consent=True,
        moderator_assignment_confirmed=True,
        provider_write_enabled=True,
        mode=ModerationMode.AUTO_PROTECT,
    )
    decision = moderator.decide(
        authorization,
        ModerationSignal(category="spam", confidence=0.99, severity=2),
        TikTokLiveConnectorCapabilities(
            approved_transport=False,
            can_mute=True,
        ),
    )
    assert decision.action is ModerationAction.RECOMMEND_MUTE
    assert decision.provider_write_permitted is False
    assert decision.requires_human_confirmation is True


def test_auto_protect_can_take_only_bounded_action_through_approved_connector():
    moderator = AuraLiveModerator()
    authorization = AuraModeratorAuthorization(
        creator_handle="creator.one",
        creator_consent=True,
        moderator_assignment_confirmed=True,
        provider_write_enabled=True,
        mode=ModerationMode.AUTO_PROTECT,
    )
    decision = moderator.decide(
        authorization,
        ModerationSignal(category="spam", confidence=0.99, severity=2),
        TikTokLiveConnectorCapabilities(
            approved_transport=True,
            can_read_live_comments=True,
            can_mute=True,
        ),
    )
    assert decision.action is ModerationAction.RECOMMEND_MUTE
    assert decision.provider_write_permitted is True
    assert decision.requires_human_confirmation is False


def test_missing_specific_connector_capability_fails_closed():
    moderator = AuraLiveModerator()
    authorization = AuraModeratorAuthorization(
        creator_handle="creator.one",
        creator_consent=True,
        moderator_assignment_confirmed=True,
        provider_write_enabled=True,
        mode=ModerationMode.AUTO_PROTECT,
    )
    decision = moderator.decide(
        authorization,
        ModerationSignal(category="harassment", confidence=0.99, severity=3),
        TikTokLiveConnectorCapabilities(
            approved_transport=True,
            can_mute=True,
            can_block=False,
        ),
    )
    assert decision.action is ModerationAction.RECOMMEND_BLOCK
    assert decision.provider_write_permitted is False
    assert decision.requires_human_confirmation is True


def test_high_severity_threat_doxxing_and_grooming_always_escalate_to_human():
    moderator = AuraLiveModerator()
    authorization = AuraModeratorAuthorization(
        creator_handle="creator.one",
        creator_consent=True,
        moderator_assignment_confirmed=True,
        provider_write_enabled=True,
        mode=ModerationMode.AUTO_PROTECT,
    )
    capabilities = TikTokLiveConnectorCapabilities(
        approved_transport=True,
        can_block=True,
        can_mute=True,
    )
    for category in ("threat", "doxxing", "grooming_concern"):
        decision = moderator.decide(
            authorization,
            ModerationSignal(category=category, confidence=0.99, severity=4),
            capabilities,
        )
        assert decision.action is ModerationAction.ESCALATE
        assert decision.provider_write_permitted is False
        assert decision.requires_human_confirmation is True


def test_low_confidence_model_output_cannot_become_a_provider_action():
    moderator = AuraLiveModerator()
    authorization = AuraModeratorAuthorization(
        creator_handle="creator.one",
        creator_consent=True,
        moderator_assignment_confirmed=True,
        provider_write_enabled=True,
        mode=ModerationMode.AUTO_PROTECT,
    )
    decision = moderator.decide(
        authorization,
        ModerationSignal(category="hate", confidence=0.55, severity=4),
        TikTokLiveConnectorCapabilities(
            approved_transport=True,
            can_block=True,
        ),
    )
    assert decision.action is ModerationAction.OBSERVE
    assert decision.provider_write_permitted is False

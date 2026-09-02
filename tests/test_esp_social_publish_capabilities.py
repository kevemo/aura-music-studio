import pytest

from aura_music_studio.esp_social_publish_capabilities import (
    implemented_content_types,
    resolve_publish_capability,
)
from aura_music_studio.social_management import (
    PlatformVariant,
    SocialConnection,
    SocialContent,
    SocialHouseStore,
    platform_capabilities,
)


def connection(
    platform: str,
    adapter: str,
    *,
    state: str = "connected",
    active: bool = True,
    supports_auto_publish: bool = True,
) -> SocialConnection:
    return SocialConnection(
        platform=platform,
        state=state,
        supports_auto_publish=supports_auto_publish,
        token_secret_ref="social-token://test_provider",
        metadata={
            "publishing_adapter": adapter,
            "publishing_adapter_active": active,
        },
    )


def test_runtime_capability_surface_is_exact_and_not_planning_surface():
    assert implemented_content_types("facebook") == ["post"]
    assert implemented_content_types("instagram") == ["post", "reel"]
    assert implemented_content_types("tiktok") == ["video"]
    assert implemented_content_types("youtube") == ["short", "video"]
    assert implemented_content_types("linkedin") == []


def test_supported_instagram_reel_is_publishable():
    result = resolve_publish_capability(
        connection("instagram", "instagram_graph"),
        platform="instagram",
        content_type="reel",
    )
    assert result.publishable is True
    assert result.adapter == "instagram_graph"
    assert result.reason_codes == []


def test_instagram_story_is_planning_only_even_with_active_connection():
    result = resolve_publish_capability(
        connection("instagram", "instagram_graph"),
        platform="instagram",
        content_type="story",
    )
    assert result.publishable is False
    assert "unsupported_content_type" in result.reason_codes


def test_unknown_or_cross_platform_adapter_cannot_become_publishable():
    unknown = resolve_publish_capability(
        connection("instagram", "future_adapter"),
        platform="instagram",
        content_type="post",
    )
    assert unknown.publishable is False
    assert "adapter_unavailable" in unknown.reason_codes

    mismatch = resolve_publish_capability(
        connection("instagram", "tiktok_content_posting"),
        platform="instagram",
        content_type="reel",
    )
    assert mismatch.publishable is False
    assert "adapter_platform_mismatch" in mismatch.reason_codes


def test_disconnected_or_inactive_connection_is_never_publishable():
    disconnected = resolve_publish_capability(
        connection("youtube", "youtube_data_v3", state="expired"),
        platform="youtube",
        content_type="video",
    )
    assert disconnected.publishable is False
    assert "disconnected" in disconnected.reason_codes

    inactive = resolve_publish_capability(
        connection("tiktok", "tiktok_content_posting", active=False),
        platform="tiktok",
        content_type="video",
    )
    assert inactive.publishable is False
    assert "adapter_inactive" in inactive.reason_codes


def test_content_validation_rejects_auto_publish_for_unimplemented_surface():
    story = SocialContent(
        title="Story",
        variants=[
            PlatformVariant(
                platform="instagram",
                content_type="story",
                auto_publish=True,
            )
        ],
    )
    with pytest.raises(ValueError, match="planning-only"):
        SocialHouseStore.validate_content(story)

    planning_story = story.model_copy(deep=True)
    planning_story.variants[0].auto_publish = False
    SocialHouseStore.validate_content(planning_story)


def test_platform_capabilities_report_runtime_publish_surface():
    capabilities = platform_capabilities()
    assert capabilities["facebook"]["auto_publish_content_types"] == ["post"]
    assert capabilities["instagram"]["auto_publish_content_types"] == ["post", "reel"]
    assert capabilities["tiktok"]["auto_publish_content_types"] == ["video"]
    assert capabilities["linkedin"]["auto_publish_implemented"] is False

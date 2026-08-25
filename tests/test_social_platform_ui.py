from __future__ import annotations

from aura_music_studio.esp_social_portal_overlay import MULTI_PLATFORM_UI, PLATFORM_AWARE_UI
from aura_music_studio.social_management import (
    PlatformVariant,
    SocialContent,
    SocialHouseStore,
    platform_capabilities,
)


def test_platform_aware_social_editor_uses_private_server_registry():
    assert "/command-center/api/social/platforms" in PLATFORM_AWARE_UI
    assert "spec.content_types" in PLATFORM_AWARE_UI
    assert "spec.caption_limit" in PLATFORM_AWARE_UI
    assert "spec.max_media" in PLATFORM_AWARE_UI
    assert "official adapter + authorisation required for publishing" in PLATFORM_AWARE_UI
    assert "postPlatform" in PLATFORM_AWARE_UI
    assert "postType" in PLATFORM_AWARE_UI
    assert "MutationObserver" in PLATFORM_AWARE_UI


def test_registry_has_platform_specific_content_types_instead_of_universal_guess():
    caps = platform_capabilities()
    assert set(caps["tiktok"]["content_types"]) == {"video", "photo"}
    assert {"post", "reel", "story"}.issubset(set(caps["instagram"]["content_types"]))
    assert set(caps["youtube"]["content_types"]) == {"video", "short"}
    assert set(caps["linkedin"]["content_types"]) == {"post", "document"}
    assert caps["pinterest"]["content_types"] == ["pin"]
    assert caps["threads"]["content_types"] == ["post"]
    assert caps["x"]["content_types"] == ["post"]
    assert caps["google_business"]["content_types"] == ["post", "event", "offer"]
    assert caps["podcast"]["content_types"] == ["episode", "clip"]
    for platform, spec in caps.items():
        assert spec["planning"] is True, platform
        assert "content_types" in spec
        assert "caption_limit" in spec
        assert "max_media" in spec


def test_multi_platform_composer_creates_independent_variants_and_never_auto_publishes():
    assert "Multi-platform Post" in MULTI_PLATFORM_UI
    assert "data-multi-type" in MULTI_PLATFORM_UI
    assert "data-multi-caption" in MULTI_PLATFORM_UI
    assert "data-multi-tags" in MULTI_PLATFORM_UI
    assert "data-multi-schedule" in MULTI_PLATFORM_UI
    assert "data-multi-zone" in MULTI_PLATFORM_UI
    assert "approval?'pending_approval'" in MULTI_PLATFORM_UI
    assert "anyScheduled?'scheduled':'draft'" in MULTI_PLATFORM_UI
    assert "auto_publish:false" in MULTI_PLATFORM_UI
    assert "official publishing adapter is authorised" in MULTI_PLATFORM_UI


def test_backend_accepts_valid_cross_platform_variants_and_rejects_invalid_pair():
    content = SocialContent(
        title="Cross-platform launch",
        variants=[
            PlatformVariant(platform="tiktok", content_type="video", caption="TikTok version"),
            PlatformVariant(platform="instagram", content_type="reel", caption="Instagram version"),
            PlatformVariant(platform="youtube", content_type="short", caption="YouTube version"),
        ],
    )
    SocialHouseStore.validate_content(content)
    assert all(variant.auto_publish is False for variant in content.variants)

    invalid = SocialContent(
        title="Invalid pairing",
        variants=[PlatformVariant(platform="linkedin", content_type="reel", caption="No")],
    )
    try:
        SocialHouseStore.validate_content(invalid)
    except ValueError as exc:
        assert "Unsupported linkedin content type" in str(exc)
    else:
        raise AssertionError("Invalid platform/content-type pairing should be rejected")

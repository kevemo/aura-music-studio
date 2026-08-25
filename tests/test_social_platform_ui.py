from __future__ import annotations

from aura_music_studio.esp_social_portal_overlay import PLATFORM_AWARE_UI
from aura_music_studio.social_management import platform_capabilities


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

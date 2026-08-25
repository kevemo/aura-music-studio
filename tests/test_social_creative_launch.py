from __future__ import annotations

from aura_music_studio.esp_social_creative_launch import SCRIPT, router
from aura_music_studio.social_management import PlatformVariant, SocialContent, SocialHouseStore


def test_creative_social_launch_route_is_private_workspace_surface():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/command-center/social/creative-launch" in paths
    assert "const SOCIAL='/command-center/api/social'" in SCRIPT
    assert "req(SOCIAL+'/spaces')" in SCRIPT
    assert "CREATIVE='/creative'" in SCRIPT
    assert "active_element_ids" in SCRIPT
    assert "source_creative_project" in SCRIPT
    assert "source_creative_element_ids" in SCRIPT
    assert "media_refs:[]" in SCRIPT
    assert "auto_publish:false" in SCRIPT
    assert "Created from Creative → Social Launch provenance bridge." in SCRIPT


def test_social_content_can_store_creative_dna_provenance_without_media_path_copying():
    content = SocialContent(
        title="Song launch",
        source_creative_project="sparkles",
        source_creative_element_ids=["el_master", "el_cover"],
        variants=[
            PlatformVariant(platform="tiktok", content_type="video", caption="Launch clip"),
            PlatformVariant(platform="instagram", content_type="reel", caption="Launch reel"),
        ],
    )
    SocialHouseStore.validate_content(content)
    assert content.source_creative_project == "sparkles"
    assert content.source_creative_element_ids == ["el_master", "el_cover"]
    assert all(variant.media_refs == [] for variant in content.variants)
    assert all(variant.auto_publish is False for variant in content.variants)

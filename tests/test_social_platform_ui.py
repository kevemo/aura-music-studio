from __future__ import annotations

from fastapi.responses import HTMLResponse

import aura_music_studio.esp_social_portal_overlay as overlay_module
from aura_music_studio.esp_social_portal_overlay import (
    MULTI_PLATFORM_UI,
    NICHE_COACH_UI,
    PLATFORM_AWARE_UI,
    TRUTH_REPLACEMENTS,
)
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
    assert "auto_publish_content_types" in PLATFORM_AWARE_UI
    assert "runtime publishing" in PLATFORM_AWARE_UI
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
        assert "auto_publish_implemented" in spec
        assert "auto_publish_content_types" in spec
        assert "publishing_adapters" in spec


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
    assert "Provider-authorised publishing" in MULTI_PLATFORM_UI


def test_aura_niche_coach_builds_repeat_safe_durable_campaign_and_tasks():
    assert "/command-center/api/social-intelligence" in NICHE_COACH_UI
    assert "/aura-insights" in NICHE_COACH_UI
    assert "/projects" in NICHE_COACH_UI
    assert "/tasks" in NICHE_COACH_UI
    assert "aura-plan" in NICHE_COACH_UI
    assert "niche-growth" in NICHE_COACH_UI
    assert "window.runAuraNicheCoach=build" in NICHE_COACH_UI
    assert "existingProject" in NICHE_COACH_UI
    assert "no duplicate tasks were created" in NICHE_COACH_UI
    assert "next integration stage" not in NICHE_COACH_UI


def test_social_overlay_replaces_placeholder_future_copy_and_exposes_connections(monkeypatch):
    original = """<!doctype html><html><body>
<a class='btn optional' href='/command-center/niche'>Change Niche</a>
<button class="btn" onclick="notice('Aura niche campaign generation is in the next integration stage.')">Plan niche campaign</button>
<p>This truthful calendar is the surface the external Calendar interfaces and later provider-authorised publishing adapters build on.</p>
<p>Aura will be able to layer recommendations on this structure without bypassing approvals.</p>
<p>The API already supports task creation/update; this view exposes the operational foundation.</p>
<p>Roadmap layer: future approval-link workflows still remain approval-state based.</p>
<p>Future authorised Social Inbox (comments/DMs via platform APIs only after permission)</p>
<footer>Powered by Aura AI Systems</footer>
</body></html>"""
    monkeypatch.setattr(overlay_module, "base_social_house", lambda _request: HTMLResponse(original))

    response = overlay_module.social_house_with_intelligence(object())
    html = response.body.decode("utf-8")

    assert "next integration stage" not in html
    assert "later provider-authorised" not in html
    assert "will be able" not in html
    assert "operational foundation" not in html
    assert "Roadmap layer" not in html
    assert "Future authorised Social Inbox" not in html
    assert "Powered by Aura AI Systems" not in html
    assert "Powered by Aura AI" in html
    assert "id=\"auraNicheCoachButton\"" in html
    assert "runAuraNicheCoach()" in html
    assert "href='/command-center/social/connections'" in html
    assert "href='/command-center/social/publish-queue'" in html
    assert "id=\"espAuraNicheCoach\"" in html
    assert len(TRUTH_REPLACEMENTS) >= 6


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

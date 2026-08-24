from __future__ import annotations

import pytest

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_social_intelligence import EspSocialIntelligenceStore
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import PlatformVariant, SocialContent, SocialHouseStore


def _active(accounts: AccountStore, email: str, name: str) -> dict:
    signup = accounts.signup(email, name, "a-very-secure-test-password", "free")
    return accounts.decide_membership(signup.approval_token, "approve", "Test Owner")


def _house_for(user_id: str, root, monkeypatch):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(root))
    token = set_current_user_id(user_id)
    try:
        store = SocialHouseStore()
        house = store.create_space("Creator Social House", "Private ESP workspace")
        return store, house
    finally:
        reset_current_user_id(token)


def test_media_library_requires_rights_confirmation(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user = _active(accounts, "creator@example.com", "Creator")
    intelligence = EspSocialIntelligenceStore(accounts)

    with pytest.raises(ValueError, match="Rights/authorization confirmation"):
        intelligence.add_media(
            user["id"],
            "space_1",
            label="Unconfirmed artwork",
            kind="image",
            source_ref="creative/project/output/cover.png",
            rights_confirmed=False,
        )

    media = intelligence.add_media(
        user["id"],
        "space_1",
        label="Owned artwork",
        kind="image",
        source_ref="creative/project/output/cover.png",
        folder="Release",
        tags=["cover", "single"],
        rights_confirmed=True,
    )
    assert media["rights_confirmed"] is True
    assert media["tags"] == ["cover", "single"]


def test_social_analytics_are_isolated_by_user_and_space(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    first = _active(accounts, "one@example.com", "One")
    second = _active(accounts, "two@example.com", "Two")
    intelligence = EspSocialIntelligenceStore(accounts)

    intelligence.add_analytics(
        first["id"],
        "space_a",
        platform="tiktok",
        period_label="Week 1",
        metrics={"views": 1200, "completion_rate": 48},
    )
    intelligence.add_analytics(
        second["id"],
        "space_b",
        platform="instagram",
        period_label="Week 1",
        metrics={"views": 900},
    )

    assert len(intelligence.list_analytics(first["id"], "space_a")) == 1
    assert intelligence.list_analytics(first["id"], "space_b") == []
    assert intelligence.list_analytics(second["id"], "space_a") == []
    assert intelligence.list_analytics(second["id"], "space_b")[0]["platform"] == "instagram"


def test_calendar_extracts_scheduled_platform_variants(tmp_path, monkeypatch):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user = _active(accounts, "calendar@example.com", "Calendar Creator")
    store, house = _house_for(user["id"], tmp_path / "social", monkeypatch)

    scheduled = SocialContent(
        title="Release teaser",
        status="scheduled",
        variants=[
            PlatformVariant(
                platform="tiktok",
                content_type="video",
                caption="New song teaser",
                scheduled_at="2026-09-01T18:00:00+01:00",
                timezone="Europe/London",
            ),
            PlatformVariant(
                platform="instagram",
                content_type="reel",
                caption="New song teaser",
                scheduled_at="2026-09-01T18:30:00+01:00",
                timezone="Europe/London",
            ),
        ],
    )
    house = store.add_content(house.id, scheduled)
    entries = EspSocialIntelligenceStore.calendar(house)

    assert len(entries) == 2
    assert entries[0]["platform"] == "tiktok"
    assert entries[1]["platform"] == "instagram"
    assert entries[0]["content_id"] == scheduled.id


def test_aura_social_insights_use_metrics_and_niche_priority(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    user = _active(accounts, "insights@example.com", "Insights Creator")
    intelligence = EspSocialIntelligenceStore(accounts)

    intelligence.add_analytics(
        user["id"],
        "space_1",
        platform="tiktok",
        period_label="Low-performing teaser",
        metrics={
            "completion_rate": 18,
            "retention_rate": 19,
            "shares": 0,
            "saves": 0,
            "comments": 0,
            "new_followers": 0,
        },
    )
    niche_profile = {
        "catalog": {
            "training": [
                "Music discovery content, covers, originals and release storytelling",
                "Retention and repeat-viewer development",
            ]
        }
    }
    insights = intelligence.aura_insights(user["id"], "space_1", niche_profile)
    recommendations = " ".join(insights["recommendations"]).lower()

    assert insights["snapshot_count"] == 1
    assert "tiktok" in insights["platforms"]
    assert "hook" in recommendations
    assert "retention" in recommendations
    assert "share" in recommendations
    assert "niche priority" in recommendations


def test_approval_comments_are_scoped_and_resolvable(tmp_path):
    accounts = AccountStore(tmp_path / "accounts.sqlite3")
    first = _active(accounts, "first@example.com", "First")
    second = _active(accounts, "second@example.com", "Second")
    intelligence = EspSocialIntelligenceStore(accounts)

    comment = intelligence.add_comment(
        first["id"],
        "space_1",
        "post_1",
        visibility="approval",
        author="ESP Mentor",
        body="Tighten the first three seconds before approval.",
    )
    assert intelligence.comments(second["id"], "space_1", "post_1") == []
    resolved = intelligence.resolve_comment(first["id"], comment["id"])
    assert resolved["resolved"] is True
    assert intelligence.comments(first["id"], "space_1", "post_1")[0]["resolved"] is True

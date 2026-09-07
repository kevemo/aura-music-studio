import os
from pathlib import Path

import pytest

from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import (
    BrandPersona,
    PlatformVariant,
    SocialConnection,
    SocialContent,
    SocialHouseStore,
    platform_capabilities,
)


def with_user(tmp_path: Path, monkeypatch, user_id: str):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    return set_current_user_id(user_id)


def test_social_houses_are_isolated_by_member(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "user-a")
    try:
        a = SocialHouseStore().create_space("Artist A")
        assert SocialHouseStore().list_spaces()[0]["id"] == a.id
    finally:
        reset_current_user_id(token)

    token = set_current_user_id("user-b")
    try:
        assert SocialHouseStore().list_spaces() == []
        b = SocialHouseStore().create_space("Artist B")
        assert b.name == "Artist B"
    finally:
        reset_current_user_id(token)

    token = set_current_user_id("user-a")
    try:
        assert [row["name"] for row in SocialHouseStore().list_spaces()] == ["Artist A"]
    finally:
        reset_current_user_id(token)


def test_platform_caption_limits_are_enforced(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "limits")
    try:
        store = SocialHouseStore()
        house = store.create_space("Limits")
        content = SocialContent(
            title="Too long X post",
            variants=[PlatformVariant(platform="x", content_type="post", caption="x" * 281)],
        )
        with pytest.raises(ValueError, match="exceed 280"):
            store.add_content(house.id, content)
    finally:
        reset_current_user_id(token)


def test_approval_gate_blocks_future_autopublishing_until_approved(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "approval")
    try:
        store = SocialHouseStore()
        house = store.create_space("Approval Brand")
        connection = SocialConnection(
            platform="instagram",
            account_label="Brand IG",
            state="connected",
            supports_auto_publish=True,
            token_secret_ref="social-token://approval-test-instagram",
            metadata={
                "publishing_adapter": "instagram_graph",
                "publishing_adapter_active": True,
            },
        )
        store.connect_placeholder(house.id, connection)
        content = SocialContent(
            title="Launch Reel",
            status="pending_approval",
            approval_required=True,
            variants=[
                PlatformVariant(
                    platform="instagram",
                    content_type="reel",
                    caption="Launch day",
                    scheduled_at="2026-09-01T18:00:00+01:00",
                    timezone="Europe/London",
                    auto_publish=True,
                )
            ],
        )
        store.add_content(house.id, content)
        blocked = store.publishing_readiness(house.id, content.id)
        assert blocked["ready"] is False
        assert "approval gate not satisfied" in blocked["variants"][0]["reasons"]

        store.approve_content(house.id, content.id, "client@example.com")
        ready = store.publishing_readiness(house.id, content.id)
        assert ready["ready"] is True
    finally:
        reset_current_user_id(token)


def test_autopublish_never_claims_ready_without_connection(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "no-connection")
    try:
        store = SocialHouseStore()
        house = store.create_space("No Connection")
        content = SocialContent(
            title="TikTok teaser",
            status="approved",
            variants=[
                PlatformVariant(
                    platform="tiktok",
                    content_type="video",
                    scheduled_at="2026-09-01T18:00:00+01:00",
                    auto_publish=True,
                )
            ],
        )
        store.add_content(house.id, content)
        state = store.publishing_readiness(house.id, content.id)
        assert state["ready"] is False
        assert "official publishing connection unavailable" in state["variants"][0]["reasons"]
    finally:
        reset_current_user_id(token)


def test_brand_persona_stays_inside_its_social_house(tmp_path: Path, monkeypatch):
    token = with_user(tmp_path, monkeypatch, "persona")
    try:
        store = SocialHouseStore()
        first = store.create_space("Music Artist")
        second = store.create_space("Gaming Creator")
        store.update_persona(
            first.id,
            BrandPersona(
                brand_name="Music Artist",
                niche="music",
                voice="warm cinematic storytelling",
                content_pillars=["original music", "behind the scenes"],
            ),
        )
        assert store.load(first.id).persona.niche == "music"
        assert store.load(second.id).persona.niche == ""
    finally:
        reset_current_user_id(token)


def test_platform_registry_covers_major_social_planning_destinations():
    capabilities = platform_capabilities()
    for platform in (
        "instagram",
        "facebook",
        "tiktok",
        "youtube",
        "linkedin",
        "pinterest",
        "threads",
        "x",
        "google_business",
        "custom",
    ):
        assert capabilities[platform]["planning"] is True

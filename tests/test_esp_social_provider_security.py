from pathlib import Path

import pytest

from aura_music_studio.esp_social_publish_media import resolve_variant_media
from aura_music_studio.esp_social_secret_refs import resolve_social_token, social_token_env_name
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import PlatformVariant, SocialContent, SocialHouseStore


def test_social_token_refs_cannot_read_arbitrary_environment_variables(monkeypatch):
    monkeypatch.setenv("LSS_ADMIN_KEY", "must-never-be-readable-through-social-ref")
    with pytest.raises(ValueError, match="social-token"):
        resolve_social_token("env://LSS_ADMIN_KEY")
    with pytest.raises(ValueError, match="social-token"):
        resolve_social_token("LSS_ADMIN_KEY")


def test_social_token_ref_maps_only_to_dedicated_namespace(monkeypatch):
    monkeypatch.setenv("AURA_SOCIAL_TOKEN_CREATOR_ONE", "provider-access-token")
    assert social_token_env_name("social-token://creator-one") == "AURA_SOCIAL_TOKEN_CREATOR_ONE"
    assert resolve_social_token("social-token://creator-one") == "provider-access-token"


def _social_house(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    token = set_current_user_id("media-security")
    store = SocialHouseStore()
    house = store.create_space("Media Security")
    return token, store, house


def test_publish_media_accepts_only_approved_rights_confirmed_library_https(tmp_path: Path, monkeypatch):
    token, store, house = _social_house(tmp_path, monkeypatch)
    try:
        house.metadata["media_library"] = {
            "schema_version": 1,
            "folders": [],
            "assets": [
                {
                    "id": "media_good",
                    "name": "Launch Reel",
                    "kind": "video",
                    "source_type": "external_url",
                    "source_ref": "https://cdn.example.com/launch.mp4",
                    "rights_confirmed": True,
                    "approval_state": "approved",
                    "archived": False,
                }
            ],
        }
        store.save(house)
        variant = PlatformVariant(
            platform="instagram",
            content_type="reel",
            media_refs=["library:media_good"],
        )
        media = resolve_variant_media(house.id, variant, store=store)
        assert len(media) == 1
        assert media[0].public_url == "https://cdn.example.com/launch.mp4"
    finally:
        reset_current_user_id(token)


def test_publish_media_rejects_unapproved_or_non_library_references(tmp_path: Path, monkeypatch):
    token, store, house = _social_house(tmp_path, monkeypatch)
    try:
        house.metadata["media_library"] = {
            "schema_version": 1,
            "folders": [],
            "assets": [
                {
                    "id": "media_bad",
                    "name": "Unapproved",
                    "kind": "video",
                    "source_type": "external_url",
                    "source_ref": "https://cdn.example.com/unapproved.mp4",
                    "rights_confirmed": False,
                    "approval_state": "quarantined",
                    "archived": False,
                }
            ],
        }
        store.save(house)
        with pytest.raises(ValueError, match="rights-confirmed"):
            resolve_variant_media(
                house.id,
                PlatformVariant(platform="tiktok", content_type="video", media_refs=["library:media_bad"]),
                store=store,
            )
        with pytest.raises(ValueError, match="only ESP Social Media Library"):
            resolve_variant_media(
                house.id,
                PlatformVariant(platform="tiktok", content_type="video", media_refs=["https://example.com/raw.mp4"]),
                store=store,
            )
    finally:
        reset_current_user_id(token)


def test_publish_media_rejects_private_provider_pull_urls(tmp_path: Path, monkeypatch):
    token, store, house = _social_house(tmp_path, monkeypatch)
    try:
        house.metadata["media_library"] = {
            "schema_version": 1,
            "folders": [],
            "assets": [
                {
                    "id": "media_private",
                    "name": "Private URL",
                    "kind": "image",
                    "source_type": "external_url",
                    "source_ref": "https://127.0.0.1/private.jpg",
                    "rights_confirmed": True,
                    "approval_state": "approved",
                    "archived": False,
                }
            ],
        }
        store.save(house)
        with pytest.raises(ValueError, match="Local/private"):
            resolve_variant_media(
                house.id,
                PlatformVariant(platform="instagram", content_type="post", media_refs=["library:media_private"]),
                store=store,
            )
    finally:
        reset_current_user_id(token)

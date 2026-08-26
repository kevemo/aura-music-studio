import pytest
from fastapi import HTTPException

from aura_music_studio.social_management import PlatformVariant
from aura_music_studio.social_management_api import _reject_client_publish_runtime_state


def test_new_content_accepts_default_provider_runtime_state():
    _reject_client_publish_runtime_state(
        [PlatformVariant(platform="instagram", content_type="reel", caption="Hello")]
    )


def test_new_content_cannot_claim_provider_published_state():
    variant = PlatformVariant(
        platform="instagram",
        content_type="reel",
        caption="Hello",
        publish_state="published",
        external_post_id="fake-provider-id",
    )
    with pytest.raises(HTTPException) as exc:
        _reject_client_publish_runtime_state([variant])
    assert exc.value.status_code == 400
    assert "provider-managed" in str(exc.value.detail)


def test_new_content_cannot_inject_provider_confirmation_metadata():
    variant = PlatformVariant(
        platform="instagram",
        content_type="reel",
        caption="Hello",
        metadata={"published_via_adapter": "fake_adapter"},
    )
    with pytest.raises(HTTPException) as exc:
        _reject_client_publish_runtime_state([variant])
    assert exc.value.status_code == 400

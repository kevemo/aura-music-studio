import pytest
from fastapi import HTTPException

from aura_music_studio.social_management_api import (
    ConnectionRequest,
    _reject_connection_credentials,
)


def test_connection_accepts_only_social_token_alias_reference():
    body = ConnectionRequest(
        platform="instagram",
        token_secret_ref="social-token://creator-one",
        metadata={"publishing_adapter": "instagram_graph"},
    )
    _reject_connection_credentials(body)


def test_connection_rejects_raw_or_arbitrary_secret_reference():
    with pytest.raises(HTTPException) as exc:
        _reject_connection_credentials(
            ConnectionRequest(
                platform="instagram",
                token_secret_ref="env://LSS_ADMIN_KEY",
            )
        )
    assert exc.value.status_code == 400
    assert "social-token" in str(exc.value.detail)


def test_connection_rejects_raw_tokens_in_nested_metadata():
    with pytest.raises(HTTPException) as exc:
        _reject_connection_credentials(
            ConnectionRequest(
                platform="youtube",
                token_secret_ref="social-token://youtube-one",
                metadata={
                    "provider": {
                        "refresh_token": "must-not-be-persisted",
                    }
                },
            )
        )
    assert exc.value.status_code == 400
    assert "Raw provider credentials" in str(exc.value.detail)

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_social_intelligence import EspSocialIntelligenceStore
from aura_music_studio.esp_social_provider_analytics import (
    ProviderAnalyticsError,
    ProviderAnalyticsService,
    _YOUTUBE_SCOPE,
    _get_json,
)
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import SocialConnection, SocialHouseStore
from aura_music_studio.social_management_api import router as social_api_router


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 400

    def json(self):
        return self._payload


def configure(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db_path))
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    monkeypatch.setenv("AURA_SOCIAL_ANALYTICS_MAX_ITEMS", "50")
    monkeypatch.setenv("AURA_SOCIAL_TOKEN_YT_TEST", "server-side-youtube-token")
    accounts = AccountStore(db_path)
    signup = accounts.signup(
        "analytics@example.test",
        "Analytics Creator",
        "very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Test Owner")
    return accounts, user


def make_house(user_id: str):
    token = set_current_user_id(user_id)
    try:
        store = SocialHouseStore()
        house = store.create_space("Analytics House")
        connection = SocialConnection(
            platform="youtube",
            account_label="Creator Channel",
            account_external_id="channel_123",
            state="connected",
            supports_auto_publish=True,
            token_secret_ref="social-token://yt-test",
            metadata={"oauth_scopes": [_YOUTUBE_SCOPE]},
        )
        house = store.connect_placeholder(house.id, connection)
        return house
    finally:
        reset_current_user_id(token)


def test_youtube_capability_ready_but_tiktok_truthfully_blocked_without_video_list(tmp_path, monkeypatch):
    _accounts, user = configure(tmp_path, monkeypatch)
    token = set_current_user_id(user["id"])
    try:
        house = make_house(user["id"])
        house.connections.append(
            SocialConnection(
                platform="tiktok",
                account_label="TikTok Creator",
                state="connected",
                token_secret_ref="social-token://tiktok-test",
                metadata={"oauth_scopes": ["user.info.basic", "video.publish"]},
            )
        )
        caps = ProviderAnalyticsService().capabilities(house)
    finally:
        reset_current_user_id(token)

    assert caps["youtube"]["ready"] is True
    assert caps["youtube"]["status"] == "ready"
    assert caps["tiktok"]["ready"] is False
    assert caps["tiktok"]["status"] == "blocked"
    assert caps["tiktok"]["required_scopes"] == ["video.list"]
    assert "video.list" in caps["tiktok"]["reason"]
    assert caps["instagram"]["ready"] is False
    assert caps["tokens_exposed"] is False


def test_youtube_sync_upserts_owned_video_statistics_without_exposing_token(tmp_path, monkeypatch):
    accounts, user = configure(tmp_path, monkeypatch)
    house = make_house(user["id"])
    video_views = {"video_1": 1200, "video_2": 450}

    def fake_get(url, *, params, headers, timeout):
        assert headers["Authorization"] == "Bearer server-side-youtube-token"
        assert "server-side-youtube-token" not in str(params)
        if url.endswith("/channels"):
            assert params["id"] == "channel_123"
            return FakeResponse(
                {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "uploads_123"}}}]}
            )
        if url.endswith("/playlistItems"):
            assert params["playlistId"] == "uploads_123"
            return FakeResponse(
                {
                    "items": [
                        {"contentDetails": {"videoId": "video_1"}},
                        {"contentDetails": {"videoId": "video_2"}},
                    ]
                }
            )
        if url.endswith("/videos"):
            items = []
            for video_id in params["id"].split(","):
                items.append(
                    {
                        "id": video_id,
                        "snippet": {
                            "title": f"Title {video_id}",
                            "publishedAt": "2026-08-01T12:00:00Z",
                        },
                        "statistics": {
                            "viewCount": str(video_views[video_id]),
                            "likeCount": "42",
                            "commentCount": "7",
                        },
                        "contentDetails": {"duration": "PT1M4S"},
                        "status": {"privacyStatus": "public"},
                    }
                )
            return FakeResponse({"items": items})
        raise AssertionError(f"Unexpected YouTube URL: {url}")

    monkeypatch.setattr(
        "aura_music_studio.esp_social_provider_analytics.requests.get",
        fake_get,
    )
    token = set_current_user_id(user["id"])
    try:
        service = ProviderAnalyticsService()
        first = service.sync_youtube(user_id=user["id"], house=house)
        video_views["video_1"] = 1800
        reloaded = SocialHouseStore().load(house.id)
        second = service.sync_youtube(user_id=user["id"], house=reloaded)
        snapshots = EspSocialIntelligenceStore(accounts).list_analytics(user["id"], house.id)
        saved_house = SocialHouseStore().load(house.id)
    finally:
        reset_current_user_id(token)

    assert first["inserted"] == 2
    assert first["updated"] == 0
    assert first["tokens_exposed"] is False
    assert first["advanced_youtube_analytics"] is False
    assert second["inserted"] == 0
    assert second["updated"] == 2
    assert len(snapshots) == 2
    by_source = {row["source_ref"]: row for row in snapshots}
    assert by_source["youtube:video:video_1"]["metrics"]["views"] == 1800
    assert by_source["youtube:video:video_2"]["metrics"]["likes"] == 42
    assert all("server-side-youtube-token" not in str(row) for row in snapshots)
    connection = next(item for item in saved_house.connections if item.platform == "youtube")
    assert connection.supports_analytics is True
    assert connection.metadata["analytics_adapter"] == "youtube_data_v3_statistics"


def test_provider_429_is_retryable_without_echoing_provider_body(monkeypatch):
    monkeypatch.setattr(
        "aura_music_studio.esp_social_provider_analytics.requests.get",
        lambda *args, **kwargs: FakeResponse(
            {"error": {"message": "secret-looking-provider-error-body"}},
            status_code=429,
        ),
    )
    with pytest.raises(ProviderAnalyticsError) as excinfo:
        _get_json(
            "https://www.googleapis.com/youtube/v3/videos",
            token="server-secret",
            params={"part": "statistics", "id": "video_1"},
            provider="YouTube",
            action="video statistics",
        )
    assert excinfo.value.retryable is True
    assert excinfo.value.reconnect is False
    assert "429" in str(excinfo.value)
    assert "secret-looking-provider-error-body" not in str(excinfo.value)
    assert "server-secret" not in str(excinfo.value)


def test_provider_401_requires_reconnect(monkeypatch):
    monkeypatch.setattr(
        "aura_music_studio.esp_social_provider_analytics.requests.get",
        lambda *args, **kwargs: FakeResponse({}, status_code=401),
    )
    with pytest.raises(ProviderAnalyticsError) as excinfo:
        _get_json(
            "https://www.googleapis.com/youtube/v3/videos",
            token="expired-secret",
            params={"part": "statistics", "id": "video_1"},
            provider="YouTube",
            action="video statistics",
        )
    assert excinfo.value.reconnect is True
    assert excinfo.value.retryable is False
    assert "expired-secret" not in str(excinfo.value)


def test_provider_analytics_routes_exist_only_under_private_esp_social_api():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(social_api_router)
    client = TestClient(app, raise_server_exceptions=False)
    private_path = "/command-center/api/social/spaces/example/provider-analytics/capabilities"
    assert client.get(private_path, follow_redirects=False).status_code != 404
    assert client.get("/provider-analytics/capabilities", follow_redirects=False).status_code == 404
    assert client.get("/social/provider-analytics/capabilities", follow_redirects=False).status_code == 404

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.accounts import AccountStore
from aura_music_studio.esp_social_intelligence import EspSocialIntelligenceStore
from aura_music_studio.esp_social_provider_analytics import ProviderAnalyticsError
from aura_music_studio.esp_social_tiktok_analytics import (
    TikTokAnalyticsService,
    _list_page,
    enhanced_capabilities,
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


def configure(tmp_path: Path, monkeypatch, *, max_items: int = 25):
    db_path = tmp_path / "studio.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db_path))
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(tmp_path / "social"))
    monkeypatch.setenv("AURA_SOCIAL_ANALYTICS_MAX_ITEMS", str(max_items))
    monkeypatch.setenv("AURA_SOCIAL_TOKEN_TT_TEST", "server-side-tiktok-token")
    accounts = AccountStore(db_path)
    signup = accounts.signup(
        "tiktok-analytics@example.test",
        "TikTok Analytics Creator",
        "very-secure-test-password",
        "free",
    )
    user = accounts.decide_membership(signup.approval_token, "approve", "Test Owner")
    return accounts, user


def make_house(user_id: str, *, scopes=None):
    token = set_current_user_id(user_id)
    try:
        store = SocialHouseStore()
        house = store.create_space("TikTok Analytics House")
        connection = SocialConnection(
            id="conn_tiktok_stats",
            platform="tiktok",
            account_label="TikTok Creator",
            account_external_id="open_tiktok_creator",
            state="connected",
            supports_auto_publish=True,
            token_secret_ref="social-token://tt-test",
            metadata={
                "oauth_verified": True,
                "oauth_provider": "tiktok",
                "oauth_scopes": scopes or ["user.info.basic", "video.publish", "video.list"],
            },
        )
        return store.connect_placeholder(house.id, connection)
    finally:
        reset_current_user_id(token)


def test_tiktok_capability_becomes_ready_only_with_video_list(tmp_path, monkeypatch):
    _accounts, user = configure(tmp_path, monkeypatch)
    token = set_current_user_id(user["id"])
    try:
        blocked = make_house(user["id"], scopes=["user.info.basic", "video.publish"])
        blocked_cap = enhanced_capabilities(blocked)["tiktok"]
        ready = make_house(user["id"])
        ready_cap = enhanced_capabilities(ready)["tiktok"]
    finally:
        reset_current_user_id(token)

    assert blocked_cap["ready"] is False
    assert blocked_cap["status"] == "blocked"
    assert blocked_cap["required_scopes"] == ["video.list"]
    assert ready_cap["ready"] is True
    assert ready_cap["status"] == "ready"
    assert ready_cap["connection_id"] == "conn_tiktok_stats"
    assert ready_cap["tokens_exposed"] is False


def test_missing_video_list_blocks_before_provider_network(tmp_path, monkeypatch):
    _accounts, user = configure(tmp_path, monkeypatch)
    house = make_house(user["id"], scopes=["user.info.basic", "video.publish"])
    monkeypatch.setattr(
        "aura_music_studio.esp_social_tiktok_analytics.requests.post",
        lambda *args, **kwargs: pytest.fail("TikTok network must not be called without video.list"),
    )
    token = set_current_user_id(user["id"])
    try:
        with pytest.raises(ProviderAnalyticsError, match="video.list"):
            TikTokAnalyticsService().sync(user_id=user["id"], house=house)
    finally:
        reset_current_user_id(token)


def test_tiktok_sync_paginates_upserts_and_never_exposes_token(tmp_path, monkeypatch):
    accounts, user = configure(tmp_path, monkeypatch, max_items=25)
    house = make_house(user["id"])
    views = {str(index): index * 100 for index in range(1, 26)}
    calls = []

    def page(ids, cursor, has_more):
        return FakeResponse(
            {
                "data": {
                    "videos": [
                        {
                            "id": video_id,
                            "title": f"TikTok {video_id}",
                            "video_description": f"Description {video_id}",
                            "create_time": 1780000000 + int(video_id),
                            "duration": 30,
                            "like_count": 10,
                            "comment_count": 3,
                            "share_count": 2,
                            "view_count": views[video_id],
                            "is_aigc": False,
                        }
                        for video_id in ids
                    ],
                    "cursor": cursor,
                    "has_more": has_more,
                },
                "error": {"code": "ok", "message": "", "log_id": "safe-log"},
            }
        )

    def fake_post(url, *, params, json, headers, timeout):
        calls.append({"url": url, "params": params, "json": dict(json), "headers": dict(headers)})
        assert url.endswith("/v2/video/list/")
        assert headers["Authorization"] == "Bearer server-side-tiktok-token"
        assert "server-side-tiktok-token" not in str(params)
        assert "server-side-tiktok-token" not in str(json)
        fields = set(params["fields"].split(","))
        assert {"id", "view_count", "like_count", "comment_count", "share_count"} <= fields
        if "cursor" not in json:
            assert json["max_count"] == 20
            return page([str(i) for i in range(1, 21)], 1700000000000, True)
        assert json["cursor"] == 1700000000000
        assert json["max_count"] == 5
        return page([str(i) for i in range(21, 26)], 1690000000000, False)

    monkeypatch.setattr("aura_music_studio.esp_social_tiktok_analytics.requests.post", fake_post)
    token = set_current_user_id(user["id"])
    try:
        service = TikTokAnalyticsService()
        first = service.sync(user_id=user["id"], house=house)
        views["1"] = 9999
        reloaded = SocialHouseStore().load(house.id)
        second = service.sync(user_id=user["id"], house=reloaded)
        snapshots = EspSocialIntelligenceStore(accounts).list_analytics(user["id"], house.id, limit=100)
        saved_house = SocialHouseStore().load(house.id)
    finally:
        reset_current_user_id(token)

    assert len(calls) == 4
    assert first["snapshots_synced"] == 25
    assert first["inserted"] == 25
    assert first["updated"] == 0
    assert first["tokens_exposed"] is False
    assert first["public_video_statistics"] is True
    assert first["advanced_tiktok_analytics"] is False
    assert second["inserted"] == 0
    assert second["updated"] == 25
    assert len(snapshots) == 25
    by_source = {row["source_ref"]: row for row in snapshots}
    assert by_source["tiktok:video:1"]["metrics"]["views"] == 9999
    assert by_source["tiktok:video:2"]["metrics"]["shares"] == 2
    assert all("server-side-tiktok-token" not in str(row) for row in snapshots)
    connection = next(item for item in saved_house.connections if item.platform == "tiktok")
    assert connection.supports_analytics is True
    assert connection.metadata["analytics_adapter"] == "tiktok_display_api_video_list"


def test_tiktok_429_is_retryable_without_echoing_provider_body(monkeypatch):
    monkeypatch.setattr(
        "aura_music_studio.esp_social_tiktok_analytics.requests.post",
        lambda *args, **kwargs: FakeResponse(
            {"error": {"code": "rate_limit_exceeded", "message": "secret-looking-body"}},
            status_code=429,
        ),
    )
    with pytest.raises(ProviderAnalyticsError) as captured:
        _list_page("private-token", max_count=20)
    assert captured.value.retryable is True
    assert captured.value.reconnect is False
    assert "429" in str(captured.value)
    assert "secret-looking-body" not in str(captured.value)
    assert "private-token" not in str(captured.value)


def test_tiktok_401_requires_reauthorisation(monkeypatch):
    monkeypatch.setattr(
        "aura_music_studio.esp_social_tiktok_analytics.requests.post",
        lambda *args, **kwargs: FakeResponse({}, status_code=401),
    )
    with pytest.raises(ProviderAnalyticsError) as captured:
        _list_page("expired-token", max_count=20)
    assert captured.value.reconnect is True
    assert captured.value.retryable is False
    assert "expired-token" not in str(captured.value)


def test_tiktok_application_scope_error_requires_reauthorisation(monkeypatch):
    monkeypatch.setattr(
        "aura_music_studio.esp_social_tiktok_analytics.requests.post",
        lambda *args, **kwargs: FakeResponse(
            {"data": {}, "error": {"code": "scope_not_authorized", "message": "do-not-echo"}}
        ),
    )
    with pytest.raises(ProviderAnalyticsError) as captured:
        _list_page("private-token", max_count=20)
    assert captured.value.reconnect is True
    assert "scope_not_authorized" in str(captured.value)
    assert "do-not-echo" not in str(captured.value)
    assert "private-token" not in str(captured.value)


def test_live_tiktok_routes_precede_old_placeholder_and_remain_private():
    """Use request matching and source order; FastAPI 0.141 hides nested route objects."""
    source = Path("aura_music_studio/esp_social_publish_queue_routes.py").read_text(encoding="utf-8")
    assert source.index("router.include_router(tiktok_analytics_router)") < source.index(
        "router.include_router(provider_analytics_router)"
    )

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(social_api_router)
    client = TestClient(app, raise_server_exceptions=False)

    private_sync = client.post(
        "/command-center/api/social/spaces/example/provider-analytics/tiktok/sync"
    )
    private_caps = client.get(
        "/command-center/api/social/spaces/example/provider-analytics/capabilities"
    )
    assert private_sync.status_code != 404
    assert private_caps.status_code != 404
    assert client.post("/spaces/example/provider-analytics/tiktok/sync").status_code == 404
    assert client.post("/social/spaces/example/provider-analytics/tiktok/sync").status_code == 404

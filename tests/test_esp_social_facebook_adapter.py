import pytest

from aura_music_studio.esp_social_facebook_adapter import FacebookPagesAdapter
from aura_music_studio.esp_social_provider_adapters import ProviderAdapterError
from aura_music_studio.esp_social_publish_media import ResolvedPublishMedia
from aura_music_studio.esp_social_publish_worker import _lookup_runtime
from aura_music_studio.social_management import (
    PlatformVariant,
    SocialConnection,
    SocialContent,
    SocialHouse,
)


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.is_error = not 200 <= status_code < 400

    def json(self):
        return self._payload


class FakeFacebookClient:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, headers=None, data=None):
        self.calls.append(("POST", url, dict(headers or {}), dict(data or {})))
        return FakeResponse({"id": "123456789012345_987654321098765"})

    def get(self, url, *, headers=None, params=None):
        self.calls.append(("GET", url, dict(headers or {}), dict(params or {})))
        return FakeResponse(
            {
                "id": "123456789012345_987654321098765",
                "permalink_url": "https://www.facebook.com/123456789012345/posts/987654321098765",
            }
        )


def _connection():
    return SocialConnection(
        platform="facebook",
        account_external_id="123456789012345",
        state="connected",
        supports_auto_publish=True,
        token_secret_ref="social-token://facebook-page-test",
        metadata={
            "publishing_adapter": "facebook_pages_graph",
            "publishing_adapter_active": True,
            "facebook_page_id": "123456789012345",
        },
    )


def test_facebook_page_text_publish_uses_bearer_header_not_request_body(monkeypatch):
    monkeypatch.setenv("AURA_FACEBOOK_GRAPH_VERSION", "v23.0")
    fake = FakeFacebookClient()
    monkeypatch.setattr(
        "aura_music_studio.esp_social_facebook_adapter.httpx.Client",
        lambda **kwargs: fake,
    )

    result = FacebookPagesAdapter().start(
        token="page-access-token",
        connection=_connection(),
        content=SocialContent(title="Launch"),
        variant=PlatformVariant(
            platform="facebook",
            content_type="post",
            caption="New release",
            hashtags=["ESP"],
            auto_publish=True,
        ),
        media=[],
    )

    method, url, headers, data = fake.calls[0]
    assert method == "POST"
    assert url == "https://graph.facebook.com/v23.0/123456789012345/feed"
    assert headers == {"Authorization": "Bearer page-access-token"}
    assert data == {"message": "New release #ESP"}
    assert "access_token" not in data
    assert result.state == "published"
    assert result.external_post_id == "123456789012345_987654321098765"


def test_facebook_page_single_image_requires_provider_accessible_https(monkeypatch):
    monkeypatch.setenv("AURA_FACEBOOK_GRAPH_VERSION", "v23.0")
    fake = FakeFacebookClient()
    monkeypatch.setattr(
        "aura_music_studio.esp_social_facebook_adapter.httpx.Client",
        lambda **kwargs: fake,
    )
    variant = PlatformVariant(
        platform="facebook",
        content_type="post",
        caption="Poster",
        auto_publish=True,
    )

    with pytest.raises(ProviderAdapterError, match="HTTPS"):
        FacebookPagesAdapter().start(
            token="page-access-token",
            connection=_connection(),
            content=SocialContent(title="Poster"),
            variant=variant,
            media=[
                ResolvedPublishMedia(
                    asset_id="media_local",
                    kind="image",
                    source_type="creative_element",
                    local_path="/tmp/not-provider-visible.png",
                    mime_type="image/png",
                )
            ],
        )

    result = FacebookPagesAdapter().start(
        token="page-access-token",
        connection=_connection(),
        content=SocialContent(title="Poster"),
        variant=variant,
        media=[
            ResolvedPublishMedia(
                asset_id="media_public",
                kind="image",
                source_type="creative_element",
                public_url="https://media.example.test/poster.png",
                mime_type="image/png",
            )
        ],
    )
    assert fake.calls[-1][1].endswith("/123456789012345/photos")
    assert fake.calls[-1][3]["published"] == "true"
    assert result.metadata["kind"] == "photo"


def test_facebook_pages_adapter_refuses_unimplemented_reels_and_profiles(monkeypatch):
    monkeypatch.setenv("AURA_FACEBOOK_GRAPH_VERSION", "v23.0")
    with pytest.raises(ProviderAdapterError, match="Reels and Stories remain disabled"):
        FacebookPagesAdapter().start(
            token="page-access-token",
            connection=_connection(),
            content=SocialContent(title="Reel"),
            variant=PlatformVariant(platform="facebook", content_type="reel", auto_publish=True),
            media=[],
        )

    bad = _connection().model_copy(update={"account_external_id": "me", "metadata": {"publishing_adapter": "facebook_pages_graph"}})
    with pytest.raises(ProviderAdapterError, match="numeric Facebook Page ID"):
        FacebookPagesAdapter().start(
            token="page-access-token",
            connection=bad,
            content=SocialContent(title="Profile"),
            variant=PlatformVariant(platform="facebook", content_type="post", caption="No", auto_publish=True),
            media=[],
        )


def test_publish_worker_resolves_facebook_adapter_without_widening_platform(monkeypatch):
    monkeypatch.setenv("AURA_FACEBOOK_GRAPH_VERSION", "v23.0")
    monkeypatch.setenv("AURA_SOCIAL_TOKEN_FACEBOOK_PAGE_TEST", "deployment-page-token")
    content = SocialContent(
        title="Facebook Page",
        variants=[PlatformVariant(platform="facebook", content_type="post", caption="Hello")],
    )
    house = SocialHouse(name="ESP", content=[content], connections=[_connection()])

    class Store:
        def load(self, space_id):
            assert space_id == house.id
            return house

    monkeypatch.setattr(
        "aura_music_studio.esp_social_publish_worker.resolve_social_token",
        lambda secret_ref: "resolved-page-token",
    )
    _, _, variant, connection, adapter, token = _lookup_runtime(
        Store(), house.id, f"{content.id}--0"
    )
    assert variant.platform == "facebook"
    assert connection.platform == "facebook"
    assert isinstance(adapter, FacebookPagesAdapter)
    assert adapter.platform == "facebook"
    assert token == "resolved-page-token"

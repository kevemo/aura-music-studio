from __future__ import annotations

import pytest

from aura_music_studio.esp_social_provider_adapters import provider_adapter
from aura_music_studio.esp_social_publish_capabilities import (
    implemented_content_types,
    resolve_publish_capability,
)
from aura_music_studio.esp_social_publish_media import ResolvedPublishMedia
from aura_music_studio.esp_social_threads_adapter import ThreadsGraphAdapter
from aura_music_studio.social_management import PlatformVariant, SocialConnection, SocialContent


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.is_error = not 200 <= status_code < 400

    def json(self):
        return self._payload


class FakeThreadsClient:
    def __init__(self, *, status="FINISHED"):
        self.status = status
        self.posts = []
        self.gets = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, headers=None, params=None):
        self.posts.append((url, dict(headers or {}), dict(params or {})))
        if url.endswith("/me/threads"):
            return FakeResponse({"id": "container_123"})
        if url.endswith("/me/threads_publish"):
            assert params == {"creation_id": "container_123"}
            return FakeResponse({"id": "thread_456"})
        raise AssertionError(url)

    def get(self, url, *, headers=None, params=None):
        self.gets.append((url, dict(headers or {}), dict(params or {})))
        assert url.endswith("/container_123")
        assert params == {"fields": "id,status"}
        return FakeResponse({"id": "container_123", "status": self.status})


def _connection() -> SocialConnection:
    return SocialConnection(
        platform="threads",
        state="connected",
        supports_auto_publish=True,
        token_secret_ref="social-oauth://threads_test",
        metadata={
            "publishing_adapter": "threads_graph",
            "publishing_adapter_active": True,
        },
    )


def _content() -> SocialContent:
    return SocialContent(title="Threads provider test")


def test_threads_runtime_capability_is_post_only_and_registered():
    assert implemented_content_types("threads") == ["post"]
    adapter = provider_adapter("threads_graph")
    assert isinstance(adapter, ThreadsGraphAdapter)
    result = resolve_publish_capability(
        _connection(),
        platform="threads",
        content_type="post",
    )
    assert result.publishable is True
    assert result.adapter == "threads_graph"
    assert result.reason_codes == []

    unsupported = resolve_publish_capability(
        _connection(),
        platform="threads",
        content_type="carousel",
    )
    assert unsupported.publishable is False
    assert "unsupported_content_type" in unsupported.reason_codes


def test_threads_text_post_uses_container_then_provider_confirmed_publish(monkeypatch):
    fake = FakeThreadsClient()
    monkeypatch.setattr(
        "aura_music_studio.esp_social_threads_adapter.httpx.Client",
        lambda **kwargs: fake,
    )
    adapter = ThreadsGraphAdapter()
    variant = PlatformVariant(
        platform="threads",
        content_type="post",
        caption="A bounded Threads post",
        hashtags=["AuraAI"],
        auto_publish=True,
    )

    started = adapter.start(
        token="secret-token",
        connection=_connection(),
        content=_content(),
        variant=variant,
        media=[],
    )
    assert started.state == "pending"
    assert started.provider_job_id == "container_123"
    create_url, headers, params = fake.posts[0]
    assert create_url == "https://graph.threads.net/me/threads"
    assert headers == {"Authorization": "Bearer secret-token"}
    assert params["media_type"] == "TEXT"
    assert params["text"] == "A bounded Threads post #AuraAI"
    assert "secret-token" not in repr(params)

    published = adapter.poll(
        token="secret-token",
        connection=_connection(),
        content=_content(),
        variant=variant,
        provider_job_id="container_123",
        provider_metadata=started.metadata,
    )
    assert published.state == "published"
    assert published.external_post_id == "thread_456"
    assert published.metadata["provider_confirmation_kind"] == "post_id"
    assert fake.posts[-1][0] == "https://graph.threads.net/me/threads_publish"


def test_threads_image_post_requires_public_provider_url_and_bounded_alt_text(monkeypatch):
    fake = FakeThreadsClient(status="IN_PROGRESS")
    monkeypatch.setattr(
        "aura_music_studio.esp_social_threads_adapter.httpx.Client",
        lambda **kwargs: fake,
    )
    adapter = ThreadsGraphAdapter()
    variant = PlatformVariant(
        platform="threads",
        content_type="post",
        caption="Image post",
        metadata={"threads_alt_text": "Accessible image description"},
    )
    image = ResolvedPublishMedia(
        asset_id="asset_image",
        kind="image",
        source_type="external_url",
        public_url="https://cdn.example.test/image.jpg",
        mime_type="image/jpeg",
    )
    started = adapter.start(
        token="secret-token",
        connection=_connection(),
        content=_content(),
        variant=variant,
        media=[image],
    )
    params = fake.posts[0][2]
    assert params["media_type"] == "IMAGE"
    assert params["image_url"] == "https://cdn.example.test/image.jpg"
    assert params["alt_text"] == "Accessible image description"

    pending = adapter.poll(
        token="secret-token",
        connection=_connection(),
        content=_content(),
        variant=variant,
        provider_job_id=started.provider_job_id or "",
        provider_metadata=started.metadata,
    )
    assert pending.state == "pending"
    assert len(fake.posts) == 1

    local_only = image.model_copy(update={"public_url": None, "local_path": "/tmp/image.jpg"})
    with pytest.raises(Exception, match="public HTTPS"):
        adapter.start(
            token="secret-token",
            connection=_connection(),
            content=_content(),
            variant=variant,
            media=[local_only],
        )


def test_threads_published_container_is_not_republished_after_worker_restart(monkeypatch):
    fake = FakeThreadsClient(status="PUBLISHED")
    monkeypatch.setattr(
        "aura_music_studio.esp_social_threads_adapter.httpx.Client",
        lambda **kwargs: fake,
    )
    adapter = ThreadsGraphAdapter()
    variant = PlatformVariant(platform="threads", content_type="post", caption="Already sent")
    result = adapter.poll(
        token="secret-token",
        connection=_connection(),
        content=_content(),
        variant=variant,
        provider_job_id="container_123",
        provider_metadata={"stage": "container"},
    )
    assert result.state == "published"
    assert result.external_post_id == "container_123"
    assert result.metadata["provider_confirmation_kind"] == "published_container_id"
    assert fake.posts == []


def test_threads_failed_or_expired_container_fails_closed(monkeypatch):
    for status in ("ERROR", "EXPIRED"):
        fake = FakeThreadsClient(status=status)
        monkeypatch.setattr(
            "aura_music_studio.esp_social_threads_adapter.httpx.Client",
            lambda **kwargs: fake,
        )
        result = ThreadsGraphAdapter().poll(
            token="secret-token",
            connection=_connection(),
            content=_content(),
            variant=PlatformVariant(platform="threads", content_type="post", caption="Test"),
            provider_job_id="container_123",
            provider_metadata={},
        )
        assert result.state == "failed"
        assert result.retryable is False

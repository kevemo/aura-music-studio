from pathlib import Path

from aura_music_studio.esp_social_provider_adapters import TikTokContentPostingAdapter
from aura_music_studio.esp_social_publish_media import ResolvedPublishMedia
from aura_music_studio.social_management import PlatformVariant, SocialConnection, SocialContent


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.is_error = not 200 <= status_code < 400

    def json(self):
        return self._payload


class FakeTikTokClient:
    def __init__(self):
        self.init_payload = None
        self.upload_headers = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, headers=None, json=None):
        if url.endswith("/creator_info/query/"):
            return FakeResponse(
                {
                    "data": {"privacy_level_options": ["SELF_ONLY"]},
                    "error": {"code": "ok"},
                }
            )
        if url.endswith("/video/init/"):
            self.init_payload = json
            return FakeResponse(
                {
                    "data": {
                        "publish_id": "publish_fake",
                        "upload_url": "https://upload.example.test/video",
                    },
                    "error": {"code": "ok"},
                }
            )
        raise AssertionError(url)

    def put(self, url, *, headers=None, content=None):
        assert url == "https://upload.example.test/video"
        self.upload_headers.append(dict(headers or {}))
        assert content
        return FakeResponse(status_code=200)


def test_tiktok_mid_sized_file_never_declares_chunk_larger_than_file(tmp_path: Path, monkeypatch):
    video = tmp_path / "seven-megabytes.mp4"
    video.write_bytes(b"x" * (7 * 1024 * 1024))
    fake = FakeTikTokClient()
    monkeypatch.setattr(
        "aura_music_studio.esp_social_provider_adapters.httpx.Client",
        lambda **kwargs: fake,
    )

    adapter = TikTokContentPostingAdapter()
    result = adapter.start(
        token="test-token",
        connection=SocialConnection(
            platform="tiktok",
            state="connected",
            supports_auto_publish=True,
        ),
        content=SocialContent(title="TikTok upload"),
        variant=PlatformVariant(
            platform="tiktok",
            content_type="video",
            auto_publish=True,
            metadata={
                "provider_user_consent": True,
                "privacy_level": "SELF_ONLY",
            },
        ),
        media=[
            ResolvedPublishMedia(
                asset_id="media_test",
                kind="video",
                source_type="creative_element",
                local_path=str(video),
                mime_type="video/mp4",
            )
        ],
    )

    source = fake.init_payload["source_info"]
    assert source["video_size"] == video.stat().st_size
    assert source["chunk_size"] == video.stat().st_size
    assert source["total_chunk_count"] == 1
    assert fake.upload_headers[0]["Content-Range"].endswith(f"/{video.stat().st_size}")
    assert result.provider_job_id == "publish_fake"

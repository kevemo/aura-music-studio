from pathlib import Path

from aura_music_studio.esp_social_provider_adapters import ProviderProgress
from aura_music_studio.esp_social_publish_worker import WorkerLease, run_publish_cycle
from aura_music_studio.request_context import reset_current_user_id, set_current_user_id
from aura_music_studio.social_management import (
    PlatformVariant,
    SocialConnection,
    SocialContent,
    SocialHouseStore,
)


class FakeInstagramAdapter:
    name = "instagram_graph"
    platform = "instagram"

    def start(self, *, token, connection, content, variant, media):
        assert token == "test-token"
        assert connection.platform == "instagram"
        assert content.title == "Worker Publish"
        assert len(media) == 1
        return ProviderProgress(
            state="published",
            provider_job_id="container_fake",
            external_post_id="ig_post_fake",
            external_post_url="https://instagram.example/ig_post_fake",
            detail="fake provider confirmation",
        )

    def poll(self, **kwargs):
        raise AssertionError("Immediate fake publication should not be polled")


def test_publish_cycle_uses_provider_confirmation_before_marking_published(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "social"
    monkeypatch.setenv("AURA_SOCIAL_ROOT", str(root))
    monkeypatch.setenv("AURA_SOCIAL_TOKEN_TEST_CREATOR", "test-token")
    monkeypatch.setenv("AURA_SOCIAL_PUBLISH_MAX_ACTIONS_PER_CYCLE", "5")
    token = set_current_user_id("worker-user")
    try:
        store = SocialHouseStore()
        house = store.create_space("Worker House")
        connection = SocialConnection(
            platform="instagram",
            account_label="Creator IG",
            account_external_id="ig_user_1",
            state="connected",
            supports_auto_publish=True,
            token_secret_ref="social-token://test_creator",
            metadata={
                "publishing_adapter": "instagram_graph",
                "publishing_adapter_active": True,
            },
        )
        store.connect_placeholder(house.id, connection)
        house = store.load(house.id)
        house.metadata["media_library"] = {
            "schema_version": 1,
            "folders": [],
            "assets": [
                {
                    "id": "media_worker",
                    "name": "Worker video",
                    "kind": "video",
                    "source_type": "external_url",
                    "source_ref": "https://cdn.example.com/worker.mp4",
                    "rights_confirmed": True,
                    "approval_state": "approved",
                    "archived": False,
                }
            ],
        }
        store.save(house)
        content = SocialContent(
            title="Worker Publish",
            status="approved",
            variants=[
                PlatformVariant(
                    platform="instagram",
                    content_type="reel",
                    scheduled_at="2000-01-01T00:00:00+00:00",
                    timezone="UTC",
                    media_refs=["library:media_worker"],
                    auto_publish=True,
                )
            ],
        )
        store.add_content(house.id, content)
    finally:
        reset_current_user_id(token)

    monkeypatch.setattr(
        "aura_music_studio.esp_social_publish_worker.provider_adapter",
        lambda name: FakeInstagramAdapter(),
    )
    result = run_publish_cycle(worker_id="worker-test")
    assert result["actions"] == 1

    token = set_current_user_id("worker-user")
    try:
        persisted = SocialHouseStore().load(house.id)
        variant = persisted.content[0].variants[0]
        assert variant.publish_state == "published"
        assert variant.external_post_id == "ig_post_fake"
        assert persisted.content[0].status == "published"
    finally:
        reset_current_user_id(token)


def test_worker_lease_prevents_two_active_publishers(tmp_path: Path):
    root = tmp_path / "social"
    first = WorkerLease(root, "worker-one", 60)
    second = WorkerLease(root, "worker-two", 60)
    same_id_second_process = WorkerLease(root, "worker-one", 60)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
        assert same_id_second_process.acquire() is False
        first.heartbeat()
    finally:
        first.release()
    assert second.acquire() is True
    second.release()

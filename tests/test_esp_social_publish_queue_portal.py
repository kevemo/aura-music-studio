from aura_music_studio.esp_social_portal_overlay import router as social_overlay_router
from aura_music_studio.esp_social_publish_queue_portal import router as publish_queue_portal_router


def test_publish_queue_portal_is_private_command_center_route():
    paths = [route.path for route in publish_queue_portal_router.routes]
    assert paths == ["/command-center/social/publish-queue"]
    assert all(path.startswith("/command-center/") for path in paths)


def test_social_overlay_exposes_publish_queue_portal():
    paths = [route.path for route in social_overlay_router.routes]
    assert "/command-center/social/publish-queue" in paths

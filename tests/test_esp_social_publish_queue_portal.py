from fastapi import FastAPI

from aura_music_studio.esp_social_portal_overlay import router as social_overlay_router
from aura_music_studio.esp_social_publish_queue_portal import router as publish_queue_portal_router


def _mounted_paths(router) -> list[str]:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    return [route.path for route in app.routes if hasattr(route, "path")]


def test_publish_queue_portal_is_private_command_center_route():
    paths = _mounted_paths(publish_queue_portal_router)
    assert paths == ["/command-center/social/publish-queue"]
    assert all(path.startswith("/command-center/") for path in paths)


def test_social_overlay_exposes_publish_queue_portal():
    paths = _mounted_paths(social_overlay_router)
    assert "/command-center/social/publish-queue" in paths

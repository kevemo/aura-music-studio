from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio.esp_social_portal_overlay import router as social_overlay_router
from aura_music_studio.esp_social_publish_queue_portal import router as publish_queue_portal_router


def _route_status(router, path: str) -> int:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    return client.get(path, follow_redirects=False).status_code


def test_publish_queue_portal_is_private_command_center_route():
    assert _route_status(publish_queue_portal_router, "/command-center/social/publish-queue") != 404
    assert _route_status(publish_queue_portal_router, "/social/publish-queue") == 404


def test_social_overlay_exposes_publish_queue_portal():
    assert _route_status(social_overlay_router, "/command-center/social/publish-queue") != 404

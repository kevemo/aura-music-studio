from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .esp_niche import require_esp_social_member
from .esp_social_provider_analytics import router as provider_analytics_router
from .esp_social_publish_queue import SocialPublishQueue
from .esp_social_threads_oauth import (
    threads_oauth_callback,
    threads_oauth_capability,
    threads_oauth_disconnect,
    threads_oauth_start,
)
from .esp_social_tiktok_analytics import router as tiktok_analytics_router
from .esp_social_tiktok_scope_upgrade import router as tiktok_scope_upgrade_router

router = APIRouter(tags=["esp-social-publish-queue"])
# Threads is registered directly rather than by nested child-router copy. This keeps the exact
# provider routes present before social_management_api flattens this router into the canonical
# /command-center/api/social surface, matching the direct-registration pattern already used there
# for Facebook and the generic OAuth providers. The exact Threads flow still performs its own ESP
# membership, CSRF-state, encrypted-vault and provider-verification checks.
router.add_api_route(
    "/oauth/threads/capability",
    threads_oauth_capability,
    methods=["GET"],
)
router.add_api_route(
    "/oauth/threads/start",
    threads_oauth_start,
    methods=["GET"],
    include_in_schema=False,
)
router.add_api_route(
    "/oauth/threads/callback",
    threads_oauth_callback,
    methods=["GET"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
router.add_api_route(
    "/oauth/threads/disconnect",
    threads_oauth_disconnect,
    methods=["POST"],
)
# The TikTok statistics extension is mounted first so its live capability/sync endpoints
# replace the earlier truthful "adapter pending" placeholders only when video.list is
# actually present. The separate scope-upgrade router remains available for explicit
# member consent after ESP's developer app has that scope approved.
router.include_router(tiktok_analytics_router)
router.include_router(provider_analytics_router)
router.include_router(tiktok_scope_upgrade_router)


def _member(request: Request):
    member, _esp_membership, _profile = require_esp_social_member(request)
    return member


def _queue() -> SocialPublishQueue:
    return SocialPublishQueue()


@router.get("/spaces/{space_id}/publish-queue")
def publish_queue(space_id: str, request: Request):
    _member(request)
    try:
        return _queue().snapshot(space_id).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/spaces/{space_id}/publish-queue/refresh")
def refresh_publish_queue(space_id: str, request: Request):
    member = _member(request)
    try:
        return _queue().refresh(space_id, actor=member.user_id).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/spaces/{space_id}/publish-queue/{entry_id}/retry")
def retry_publish_queue_entry(space_id: str, entry_id: str, request: Request):
    member = _member(request)
    try:
        return _queue().retry(space_id, entry_id, actor=member.user_id).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except KeyError as exc:
        raise HTTPException(404, "Publish queue entry not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

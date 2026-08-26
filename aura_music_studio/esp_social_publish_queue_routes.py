from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .esp_niche import require_esp_social_member
from .esp_social_publish_queue import SocialPublishQueue

router = APIRouter(tags=["esp-social-publish-queue"])


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

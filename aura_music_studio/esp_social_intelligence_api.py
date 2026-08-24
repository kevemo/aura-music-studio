from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .content_safety import enforce_creation_policy
from .esp_niche import require_esp_social_member
from .esp_social_intelligence import EspSocialIntelligenceStore
from .social_management import SocialHouseStore

router = APIRouter(prefix="/command-center/api/social-intelligence", tags=["esp-social-intelligence"])
intelligence = EspSocialIntelligenceStore()


class MediaRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["image", "video", "audio", "document", "creative_project", "other"] = "other"
    source_ref: str = Field(min_length=1, max_length=1200)
    folder: str = Field(default="", max_length=160)
    tags: list[str] = Field(default_factory=list)
    rights_confirmed: bool = False
    metadata: dict = Field(default_factory=dict)


class AnalyticsRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=80)
    period_label: str = Field(default="", max_length=180)
    content_id: str | None = Field(default=None, max_length=160)
    metrics: dict = Field(default_factory=dict)
    source_ref: str | None = Field(default=None, max_length=1200)


class CommentRequest(BaseModel):
    visibility: Literal["internal", "approval"] = "internal"
    author: str = Field(default="ESP Member", max_length=160)
    body: str = Field(min_length=1, max_length=5000)


def _context(request: Request):
    return require_esp_social_member(request)


def _house(space_id: str):
    try:
        return SocialHouseStore().load(space_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "ESP Social House not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/spaces/{space_id}/calendar")
def calendar(space_id: str, request: Request):
    _context(request)
    house = _house(space_id)
    return {"space_id": space_id, "entries": intelligence.calendar(house)}


@router.get("/spaces/{space_id}/media")
def media_library(space_id: str, request: Request, folder: str | None = None):
    member, _membership, _profile = _context(request)
    _house(space_id)
    return {"space_id": space_id, "media": intelligence.list_media(member.user_id, space_id, folder)}


@router.post("/spaces/{space_id}/media")
def add_media(space_id: str, body: MediaRequest, request: Request):
    member, _membership, _profile = _context(request)
    _house(space_id)
    if not body.rights_confirmed:
        raise HTTPException(400, "Confirm that you own or are authorised to use this media")
    try:
        enforce_creation_policy(body.label, *body.tags, context="ESP social media metadata")
        item = intelligence.add_media(
            member.user_id,
            space_id,
            label=body.label,
            kind=body.kind,
            source_ref=body.source_ref,
            folder=body.folder,
            tags=body.tags,
            rights_confirmed=body.rights_confirmed,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"media": item}


@router.get("/spaces/{space_id}/analytics")
def analytics(space_id: str, request: Request):
    member, _membership, _profile = _context(request)
    _house(space_id)
    return {"space_id": space_id, "snapshots": intelligence.list_analytics(member.user_id, space_id)}


@router.post("/spaces/{space_id}/analytics")
def add_analytics(space_id: str, body: AnalyticsRequest, request: Request):
    member, _membership, _profile = _context(request)
    _house(space_id)
    item = intelligence.add_analytics(
        member.user_id,
        space_id,
        platform=body.platform,
        period_label=body.period_label,
        content_id=body.content_id,
        metrics=body.metrics,
        source_ref=body.source_ref,
    )
    return {"snapshot": item}


@router.get("/spaces/{space_id}/aura-insights")
def aura_insights(space_id: str, request: Request):
    member, _membership, profile = _context(request)
    _house(space_id)
    return intelligence.aura_insights(member.user_id, space_id, profile)


@router.get("/spaces/{space_id}/content/{content_id}/comments")
def comments(space_id: str, content_id: str, request: Request):
    member, _membership, _profile = _context(request)
    house = _house(space_id)
    if not any(item.id == content_id for item in house.content):
        raise HTTPException(404, "Content not found")
    return {"comments": intelligence.comments(member.user_id, space_id, content_id)}


@router.post("/spaces/{space_id}/content/{content_id}/comments")
def add_comment(space_id: str, content_id: str, body: CommentRequest, request: Request):
    member, _membership, _profile = _context(request)
    house = _house(space_id)
    if not any(item.id == content_id for item in house.content):
        raise HTTPException(404, "Content not found")
    if body.visibility == "approval":
        try:
            enforce_creation_policy(body.body, context="ESP approval comment")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    item = intelligence.add_comment(
        member.user_id,
        space_id,
        content_id,
        visibility=body.visibility,
        author=body.author,
        body=body.body,
    )
    return {"comment": item}


@router.post("/comments/{comment_id}/resolve")
def resolve_comment(comment_id: str, request: Request):
    member, _membership, _profile = _context(request)
    try:
        item = intelligence.resolve_comment(member.user_id, comment_id)
    except KeyError as exc:
        raise HTTPException(404, "Comment not found") from exc
    return {"comment": item}

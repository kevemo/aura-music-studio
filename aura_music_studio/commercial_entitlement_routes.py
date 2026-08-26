from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .commercial_entitlements import (
    can_download_media,
    image_poster_usage,
    record_image_poster_generation,
    require_image_poster_generation,
    require_media_download,
)
from .creative_media_preview import creative_element_media as base_creative_element_media
from .creative_media_preview import resolve_element_media
from .creative_project import CreativeProjectStore
from .creative_project_api import QueueRendererRequest
from .creative_project_api import queue_creative_render as base_queue_creative_render
from .tenant_storage import project_path

router = APIRouter(tags=["commercial-entitlements"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    return member


def _directive_kind(project_name: str, directive_id: str) -> str | None:
    try:
        project = project_path(project_name, must_exist=True)
        manifest = CreativeProjectStore(project).load()
    except (FileNotFoundError, ValueError):
        return None
    directive = next((item for item in manifest.directives if item.id == directive_id), None)
    return str(getattr(directive, "target_kind", "") or "") if directive else None


@router.get("/creative/entitlements")
def creative_entitlements(request: Request):
    member = _member(request)
    return {
        "plan": member.plan.id,
        "image_poster": image_poster_usage(member),
        "downloads": {
            "image": can_download_media(member, "image"),
            "audio": can_download_media(member, "audio"),
            "music": can_download_media(member, "music"),
            "video": can_download_media(member, "video"),
        },
        "truthful_state": (
            "Image/poster downloads are available on all current tiers. "
            "Music/video downloads require Basic (£4.99) or Pro (£9.99)."
        ),
    }


@router.post("/creative/projects/{project_name}/directives/{directive_id}/render")
def render_with_commercial_entitlements(
    project_name: str,
    directive_id: str,
    body: QueueRendererRequest,
    request: Request,
):
    member = _member(request)
    kind = _directive_kind(project_name, directive_id)
    if kind == "image":
        try:
            require_image_poster_generation(member)
        except PermissionError as exc:
            status = 429 if "allowance reached" in str(exc).lower() else 403
            raise HTTPException(status, str(exc)) from exc

    response = base_queue_creative_render(project_name, directive_id, body, request)
    if kind == "image" and isinstance(response, dict):
        response["commercial_entitlements"] = {
            "image_poster": record_image_poster_generation(
                member,
                project_id=project_name,
                directive_id=directive_id,
            )
        }
    return response


@router.get("/creative/projects/{project_name}/elements/{element_id}/media")
def media_with_commercial_entitlements(
    project_name: str,
    element_id: str,
    request: Request,
    download: bool = False,
):
    member = _member(request)
    if download:
        try:
            _path, _media_type, element = resolve_element_media(project_name, element_id)
            require_media_download(member, str(element.get("kind") or ""))
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "Creative media file not found") from exc
        except KeyError as exc:
            raise HTTPException(404, "Creative Element not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return base_creative_element_media(project_name, element_id, request, download=download)


__all__ = ["router"]

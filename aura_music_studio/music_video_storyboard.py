from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .video_music_sync import _read as read_sync_map
from .video_scene_render import _member_identity
from .video_scene_timeline import _now, _read as read_timeline, _validate_timeline, _write as write_timeline

router = APIRouter(prefix="/creative", tags=["music-video-storyboard"])

_MAX_STORYBOARD_SCENES = 100
_MAX_SECONDS = 4 * 60 * 60


class StoryboardPlanRequest(BaseModel):
    project_end_seconds: float | None = Field(default=None, gt=0, le=_MAX_SECONDS)
    scene_prefix: str = Field(default="mv", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    visual_direction: str = Field(default="", max_length=2000)
    shot_type: str = Field(default="", max_length=120)
    camera_direction: str = Field(default="", max_length=1000)
    include_lyrics: bool = True


class StoryboardApplyRequest(StoryboardPlanRequest):
    replace_existing: bool = False


def _build_storyboard(project_name: str, body: StoryboardPlanRequest) -> dict[str, Any]:
    sync = read_sync_map(project_name)
    markers = list(sync.get("markers") or [])
    sections = sorted(
        [item for item in markers if item.get("kind") == "section"],
        key=lambda item: (float(item.get("time_seconds", 0.0)), str(item.get("id") or "")),
    )
    if not sections:
        raise HTTPException(409, "Add section markers to the video/music synchronization map before creating a music-video storyboard")
    if len(sections) > _MAX_STORYBOARD_SCENES:
        raise HTTPException(400, f"Music-video storyboards can contain at most {_MAX_STORYBOARD_SCENES} section scenes")

    lyrics = sorted(
        [item for item in markers if item.get("kind") == "lyric"],
        key=lambda item: (float(item.get("time_seconds", 0.0)), str(item.get("id") or "")),
    )
    scenes: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        start = float(section["time_seconds"])
        explicit_end = section.get("end_seconds")
        if explicit_end is not None:
            end = float(explicit_end)
        elif index + 1 < len(sections):
            end = float(sections[index + 1]["time_seconds"])
        elif body.project_end_seconds is not None:
            end = float(body.project_end_seconds)
        else:
            raise HTTPException(400, "The final section requires end_seconds or project_end_seconds before a storyboard can be planned")
        if end <= start:
            raise HTTPException(400, f"Section marker {section.get('id')} has invalid timing")

        label = str(section.get("section_label") or "Section").strip()
        lyric_lines: list[str] = []
        if body.include_lyrics:
            for marker in lyrics:
                moment = float(marker.get("time_seconds", 0.0))
                if start <= moment < end and str(marker.get("text") or "").strip():
                    lyric_lines.append(str(marker["text"]).strip())
        lyric_excerpt = " / ".join(lyric_lines)
        if len(lyric_excerpt) > 900:
            lyric_excerpt = lyric_excerpt[:897] + "..."
        description_parts = [f"Music section: {label}."]
        if lyric_excerpt:
            description_parts.append(f"Lyrics in this section: {lyric_excerpt}")
        if body.visual_direction.strip():
            description_parts.append(body.visual_direction.strip())

        timestamp = _now()
        scenes.append(
            {
                "id": f"{body.scene_prefix}-{index + 1:02d}",
                "label": label,
                "start_seconds": start,
                "end_seconds": end,
                "description": " ".join(description_parts),
                "shot_type": body.shot_type.strip(),
                "camera_direction": body.camera_direction.strip(),
                "continuity_notes": f"Planned from synchronization section marker {section['id']}; preserve unrelated established continuity unless explicitly revised.",
                "continuity_profile_ids": [],
                "preserve_element_ids": [],
                "reference_ids": [],
                "output_element_id": None,
                "status": "planned",
                "order": index,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )

    _validate_timeline(scenes)
    return {
        "project_name": project_name,
        "scenes": scenes,
        "scene_count": len(scenes),
        "source": "video_music_sync.section_markers",
        "timeline_mutated": False,
        "renderer_invoked": False,
        "frame_accurate_renderer_sync_guaranteed": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_or_membership": False,
    }


@router.post("/projects/{project_name}/music-video/storyboard-plan")
def plan_music_video_storyboard(project_name: str, body: StoryboardPlanRequest, request: Request):
    _member_identity(request)
    return _build_storyboard(project_name, body)


@router.post("/projects/{project_name}/music-video/storyboard-apply")
def apply_music_video_storyboard(project_name: str, body: StoryboardApplyRequest, request: Request):
    _member_identity(request)
    existing = read_timeline(project_name)
    if existing.get("scenes") and not body.replace_existing:
        raise HTTPException(409, "The video timeline already contains scenes. Set replace_existing=true only when you explicitly want to replace it with this storyboard.")
    plan = _build_storyboard(project_name, body)
    candidate = {"schema_version": 1, "project_name": project_name, "updated_at": _now(), "scenes": plan["scenes"]}
    _validate_timeline(candidate["scenes"])
    saved = write_timeline(project_name, candidate)
    return {
        **plan,
        "timeline": saved,
        "timeline_mutated": True,
        "existing_timeline_replaced": bool(existing.get("scenes")),
        "renderer_invoked": False,
    }


__all__ = ["router", "StoryboardPlanRequest", "StoryboardApplyRequest", "plan_music_video_storyboard", "apply_music_video_storyboard"]

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .music_video_storyboard import (
    StoryboardApplyRequest,
    StoryboardPlanRequest,
    apply_music_video_storyboard as base_apply_music_video_storyboard,
    plan_music_video_storyboard as base_plan_music_video_storyboard,
)
from .plans import VIDEO_SYNC
from .video_audio_analysis_ingestion import (
    AnalyzeProjectAudioRequest,
    analyze_project_audio as base_analyze_project_audio,
)
from .video_music_sync import (
    ApplySnapRequest,
    ReplaceSyncMapRequest,
    SnapPlanRequest,
    apply_scene_boundary_snaps as base_apply_scene_boundary_snaps,
    get_video_music_sync as base_get_video_music_sync,
    plan_scene_boundary_snaps as base_plan_scene_boundary_snaps,
    replace_video_music_sync as base_replace_video_music_sync,
)

router = APIRouter(prefix="/creative", tags=["video-sync-entitlements"])


def require_video_sync_entitlement(request: Request):
    member = getattr(request.state, "member", None)
    plan = getattr(member, "plan", None)
    if member is None or plan is None:
        raise HTTPException(401, "Authenticated membership is required")
    if not callable(getattr(plan, "has", None)) or not plan.has(VIDEO_SYNC):
        raise HTTPException(403, "Pro membership is required for video/music synchronization")
    return member


@router.get("/projects/{project_name}/video-music-sync")
def get_video_music_sync(project_name: str, request: Request):
    require_video_sync_entitlement(request)
    return base_get_video_music_sync(project_name, request)


@router.put("/projects/{project_name}/video-music-sync")
def replace_video_music_sync(project_name: str, body: ReplaceSyncMapRequest, request: Request):
    require_video_sync_entitlement(request)
    return base_replace_video_music_sync(project_name, body, request)


@router.post("/projects/{project_name}/video-music-sync/snap-plan")
def plan_scene_boundary_snaps(project_name: str, body: SnapPlanRequest, request: Request):
    require_video_sync_entitlement(request)
    return base_plan_scene_boundary_snaps(project_name, body, request)


@router.post("/projects/{project_name}/video-music-sync/apply-snaps")
def apply_scene_boundary_snaps(project_name: str, body: ApplySnapRequest, request: Request):
    require_video_sync_entitlement(request)
    return base_apply_scene_boundary_snaps(project_name, body, request)


@router.post("/projects/{project_name}/video-music-sync/analyze-audio")
def analyze_project_audio(project_name: str, body: AnalyzeProjectAudioRequest, request: Request):
    require_video_sync_entitlement(request)
    return base_analyze_project_audio(project_name, body, request)


@router.post("/projects/{project_name}/music-video/storyboard-plan")
def plan_music_video_storyboard(project_name: str, body: StoryboardPlanRequest, request: Request):
    require_video_sync_entitlement(request)
    return base_plan_music_video_storyboard(project_name, body, request)


@router.post("/projects/{project_name}/music-video/storyboard-apply")
def apply_music_video_storyboard(project_name: str, body: StoryboardApplyRequest, request: Request):
    require_video_sync_entitlement(request)
    return base_apply_music_video_storyboard(project_name, body, request)


__all__ = ["router", "require_video_sync_entitlement"]

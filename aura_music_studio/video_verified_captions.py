from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .plans import VIDEO_SYNC
from .video_music_sync import _read as read_sync
from .video_scene_render import _member_identity

router = APIRouter(tags=["video-captions"])

CaptionFormat = Literal["srt", "vtt"]
_GENERATED_SOURCE = "song-dna-verified-lyrics"
_MAX_CAPTION_CUES = 5000


def _require_pro_video_sync(request: Request):
    _member_identity(request)
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(VIDEO_SYNC):
        raise HTTPException(403, "Verified video captions require Pro")
    return member


def _caption_text(value: object) -> str:
    # Normalize embedded line breaks/extra whitespace so one lyric cannot corrupt cue structure.
    return " ".join(str(value or "").split()).strip()


def _verified_caption_cues(project_name: str) -> tuple[list[dict], dict]:
    sync = read_sync(project_name)
    cues: list[dict] = []
    skipped_without_end = 0
    skipped_invalid_range = 0

    for marker in sync.get("markers", []):
        if marker.get("kind") != "lyric" or marker.get("source_element_id") != _GENERATED_SOURCE:
            continue
        text = _caption_text(marker.get("text"))
        if not text:
            continue
        start = float(marker.get("time_seconds", 0.0))
        raw_end = marker.get("end_seconds")
        if raw_end is None:
            skipped_without_end += 1
            continue
        end = float(raw_end)
        if end <= start:
            skipped_invalid_range += 1
            continue
        cues.append({
            "id": str(marker.get("id") or ""),
            "start_seconds": start,
            "end_seconds": end,
            "text": text,
        })

    cues.sort(key=lambda item: (item["start_seconds"], item["end_seconds"], item["id"]))
    if len(cues) > _MAX_CAPTION_CUES:
        raise HTTPException(409, f"Verified caption export supports at most {_MAX_CAPTION_CUES} cues")
    if not cues:
        raise HTTPException(
            409,
            "No verified lyric lines with complete start/end timing are available for caption export",
        )

    return cues, {
        "source": _GENERATED_SOURCE,
        "verified_timing_only": True,
        "estimated_timing_used": False,
        "cue_count": len(cues),
        "skipped_without_end": skipped_without_end,
        "skipped_invalid_range": skipped_invalid_range,
    }


def _timestamp(seconds: float, *, separator: str) -> str:
    total_ms = max(0, int(round(seconds * 1000.0)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _render_srt(cues: list[dict]) -> str:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n"
            f"{_timestamp(cue['start_seconds'], separator=',')} --> {_timestamp(cue['end_seconds'], separator=',')}\n"
            f"{cue['text']}"
        )
    return "\n\n".join(blocks) + "\n"


def _render_vtt(cues: list[dict]) -> str:
    blocks = ["WEBVTT"]
    for cue in cues:
        blocks.append(
            f"{_timestamp(cue['start_seconds'], separator='.')} --> {_timestamp(cue['end_seconds'], separator='.')}\n"
            f"{cue['text']}"
        )
    return "\n\n".join(blocks) + "\n"


@router.get("/projects/{project_name}/video-captions/verified")
def preview_verified_captions(project_name: str, request: Request):
    # Entitlement/authentication deliberately run before read_sync/project lookup.
    _require_pro_video_sync(request)
    cues, evidence = _verified_caption_cues(project_name)
    return {
        "project_name": project_name,
        "cues": cues,
        "caption_evidence": evidence,
        "available_exports": ["srt", "vtt"],
        "raw_filesystem_paths_exposed": False,
        "grants_esp_role_or_permission": False,
        "alters_billing_membership_or_creation_coins": False,
    }


@router.get("/projects/{project_name}/video-captions/verified/export/{caption_format}")
def export_verified_captions(project_name: str, caption_format: CaptionFormat, request: Request):
    # The same Pro gate precedes all project/sync-map access on export requests.
    _require_pro_video_sync(request)
    cues, _evidence = _verified_caption_cues(project_name)
    if caption_format == "srt":
        content = _render_srt(cues)
        media_type = "application/x-subrip; charset=utf-8"
    else:
        content = _render_vtt(cues)
        media_type = "text/vtt; charset=utf-8"
    headers = {
        "Content-Disposition": f'attachment; filename="pulsar-verified-lyrics.{caption_format}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type=media_type, headers=headers)


__all__ = [
    "router",
    "preview_verified_captions",
    "export_verified_captions",
    "_verified_caption_cues",
    "_render_srt",
    "_render_vtt",
]

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .assets import AssetLibrary
from .tenant_storage import project_path

router = APIRouter(tags=["Voice House"])


@router.get("/projects/{project_name}/voice-house/audio-assets")
def voice_house_audio_assets(project_name: str):
    try:
        project = project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc
    return {
        "assets": [
            {
                "id": item.id,
                "name": item.name,
                "duration_seconds": item.analysis.get("duration_seconds"),
                "sample_rate": item.analysis.get("sample_rate"),
                "tags": item.tags,
            }
            for item in AssetLibrary(project).list()
            if item.kind == "audio"
        ]
    }

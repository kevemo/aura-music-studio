from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .assets import AssetLibrary
from .editing import RegionEditRequest, add_take_to_session, generate_region_take
from .layers import generate_complementary_layer
from .project import ProjectWorkspace
from .revisions import create_revision
from .session import Clip, StudioSession
from .tenant_storage import project_path

router = APIRouter(tags=["Generative DAW Editing"])


class RegionEditPayload(RegionEditRequest):
    source_asset_id: str
    track_id: str | None = None
    take_name: str | None = None


class AddTrackRequest(BaseModel):
    source_asset_id: str
    track_role: str = "guitar"
    prompt: str
    track_name: str | None = None


def _project(name: str) -> Path:
    try:
        return project_path(name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc


def _audio(project: Path, asset_id: str):
    try:
        asset = AssetLibrary(project).get(asset_id)
    except KeyError as exc:
        raise HTTPException(404, "Audio asset not found") from exc
    if asset.kind != "audio":
        raise HTTPException(400, "Generative audio editing requires a real audio asset")
    return asset


def _session(project: Path) -> tuple[StudioSession, Path]:
    path = project / "aura_session.json"
    if path.exists():
        return StudioSession.load(path), path
    session = StudioSession(name=project.name)
    session.add_track("Master", "master")
    return session, path


def _snapshot(project: Path, label: str, reason: str) -> None:
    # Pro-only edit endpoints keep a deep metadata/session undo history. Audio is never duplicated.
    try:
        create_revision(project, label=label, reason=reason, actor="Aura", keep=200)
    except Exception:
        # A missing pre-existing session/manifest must never prevent a valid first edit.
        pass


@router.post("/projects/{project_name}/region-edit")
def region_edit(project_name: str, request: RegionEditPayload):
    project = _project(project_name)
    asset = _audio(project, request.source_asset_id)
    source = project / asset.path
    workspace = ProjectWorkspace(project)
    takes = workspace.work_dir / "region_takes"
    take_number = len(list(takes.glob("take_*.wav"))) + 1 if takes.exists() else 1

    edit_request = RegionEditRequest(
        operation=request.operation,
        start_seconds=request.start_seconds,
        end_seconds=request.end_seconds,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        strength=request.strength,
        target_duration_seconds=request.target_duration_seconds,
    )
    try:
        generated = generate_region_take(source, edit_request, takes, take_number)
    except Exception as exc:
        raise HTTPException(503, f"Region generation unavailable: {type(exc).__name__}: {exc}") from exc

    response = {
        "operation": request.operation,
        "source_asset_id": asset.id,
        "take_path": str(generated),
        "audio_origin": "neural_real_audio",
        "non_destructive": True,
    }

    if request.track_id:
        session, session_path = _session(project)
        _snapshot(project, f"Before {request.operation} take", "region_edit")
        try:
            clip = add_take_to_session(
                session,
                track_id=request.track_id,
                audio_path=generated,
                name=request.take_name or f"Aura {request.operation.title()} Take {take_number}",
                generation_metadata={
                    "operation": request.operation,
                    "prompt": request.prompt,
                    "source_asset_id": asset.id,
                },
            )
        except KeyError as exc:
            raise HTTPException(404, "Session track not found") from exc
        session.save(session_path)
        response["session_clip"] = clip.model_dump()
        response["session_path"] = str(session_path)
    return response


@router.post("/projects/{project_name}/add-generated-track")
def add_generated_track(project_name: str, request: AddTrackRequest):
    project = _project(project_name)
    asset = _audio(project, request.source_asset_id)
    workspace = ProjectWorkspace(project)
    try:
        generated = generate_complementary_layer(
            project / asset.path,
            workspace,
            track=request.track_role,
            prompt=request.prompt,
        )
    except Exception as exc:
        raise HTTPException(503, f"Generated track unavailable: {type(exc).__name__}: {exc}") from exc

    session, session_path = _session(project)
    _snapshot(project, f"Before adding {request.track_role}", "add_generated_track")
    track = session.add_track(request.track_name or f"Aura {request.track_role.title()}", request.track_role)
    clip = Clip(
        name=f"Generated {request.track_role}",
        kind="audio",
        source=str(generated),
        start=0.0,
        metadata={
            "real_audio": True,
            "generated": True,
            "source_asset_id": asset.id,
            "prompt": request.prompt,
        },
    )
    track.clips.append(clip)
    session.generation_history.append(
        {
            "action": "add_generated_track",
            "track_id": track.id,
            "clip_id": clip.id,
            "role": request.track_role,
            "prompt": request.prompt,
        }
    )
    session.save(session_path)
    return {
        "track": track.model_dump(),
        "generated_audio": str(generated),
        "audio_origin": "neural_real_audio",
    }
